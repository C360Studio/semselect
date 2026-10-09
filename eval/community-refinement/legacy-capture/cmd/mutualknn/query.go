package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// Wire contract of the legacy graph-embedding similar query (semstreams
// processor/graph-embedding/query.go) and the ADR-060 header-classified error
// reply (semstreams natsclient/errors.go).
const (
	similarSubject = "graph.embedding.query.similar"

	headerStatus     = "X-Status"
	headerErrorClass = "X-Error-Class"
	headerErrorCode  = "X-Error-Code"
	statusError      = "error"
	classTransient   = "transient"

	// codeEmbeddingUnavailable is the per-entity "this source entity has no
	// usable embedding" miss. The legacy adapter treats it as a definitive empty.
	codeEmbeddingUnavailable = "embedding_unavailable"

	// maxRetries bounds retries of a transient NATS failure per entity.
	maxRetries = 2

	// maxReplyBytes bounds one similar reply; k=8 replies are well under 2 KiB.
	maxReplyBytes = 1 << 20
	// maxOutgoingValueBytes bounds one OUTGOING_INDEX value.
	maxOutgoingValueBytes = 8 << 20

	// abortFailureFraction mirrors the legacy maxTransientErrorFraction (0.10)
	// and its denominator, the ENTITY_STATES count: once more than this
	// fraction has failed, the sweep is abandoned rather than ground out
	// against a wedged responder.
	abortFailureFraction = 0.10
)

// Query outcomes recorded per entity.
const (
	statusOK          = "ok"
	statusMiss        = "embedding_unavailable"
	statusFailed      = "failed"
	statusNotQueried  = "not_queried"
	failureParse      = "parse"
	failureTransport  = "transport"
	failureHandler    = "handler"
	failureCancelled  = "cancelled"
	failureOversized  = "oversized"
	failureIDMismatch = "entity_id_mismatch"
)

// requester is the subset of *nats.Conn the sweep needs, so retry and
// classification policy can be tested without a server.
type requester interface {
	RequestMsgWithContext(ctx context.Context, msg *nats.Msg) (*nats.Msg, error)
}

type similarRequest struct {
	EntityID string `json:"entity_id"`
	Limit    int    `json:"limit"`
}

type similarResponse struct {
	EntityID string     `json:"entity_id"`
	Similar  []neighbor `json:"similar"`
	Duration string     `json:"duration"`
}

// queryResult is one line of directed.jsonl.
type queryResult struct {
	EntityID       string     `json:"entity_id"`
	Status         string     `json:"status"`
	Attempts       int        `json:"attempts"`
	LatencyMS      float64    `json:"latency_ms"`
	ServerDuration string     `json:"server_duration,omitempty"`
	Similar        []neighbor `json:"similar"`
	Directed       []neighbor `json:"directed"`
	FailureKind    string     `json:"failure_kind,omitempty"`
	ErrorClass     string     `json:"error_class,omitempty"`
	ErrorCode      string     `json:"error_code,omitempty"`
	Error          string     `json:"error,omitempty"`

	latency time.Duration
}

// isTransientTransport reports NATS failures worth retrying: a request
// timeout or no responders. Parent-context cancellation is handled before
// this is consulted.
func isTransientTransport(err error) bool {
	return errors.Is(err, nats.ErrTimeout) ||
		errors.Is(err, context.DeadlineExceeded) ||
		errors.Is(err, nats.ErrNoResponders)
}

// querySimilar issues one similar request with at most maxRetries retries of
// transient failures. A parse error, oversized reply or non-transient handler
// error is final on the first attempt.
func querySimilar(ctx context.Context, r requester, id string, k int, threshold float64,
	timeout time.Duration, backoff time.Duration,
) queryResult {
	res := queryResult{EntityID: id, Status: statusFailed, Similar: []neighbor{}, Directed: []neighbor{}}
	body, err := json.Marshal(similarRequest{EntityID: id, Limit: k})
	if err != nil {
		res.FailureKind, res.Error = failureParse, err.Error()
		return res
	}
	for attempt := 1; ; attempt++ {
		res.Attempts = attempt
		retry := func() bool {
			if attempt > maxRetries {
				return false
			}
			select {
			case <-ctx.Done():
				// The run is stopping: not a transport or handler failure, and
				// not one that may count toward the failure budget.
				res.FailureKind = failureCancelled
				res.Error = fmt.Sprintf("%v during retry backoff; last error: %s", ctx.Err(), res.Error)
				return false
			case <-time.After(time.Duration(attempt) * backoff):
				return true
			}
		}

		rctx, cancel := context.WithTimeout(ctx, timeout)
		start := time.Now()
		msg := nats.NewMsg(similarSubject)
		msg.Data = body
		reply, err := r.RequestMsgWithContext(rctx, msg)
		res.latency = time.Since(start)
		cancel()
		res.LatencyMS = float64(res.latency.Microseconds()) / 1000.0

		if err != nil {
			if ctx.Err() != nil {
				res.FailureKind, res.Error = failureCancelled, ctx.Err().Error()
				return res
			}
			res.FailureKind, res.Error = failureTransport, err.Error()
			if isTransientTransport(err) && retry() {
				continue
			}
			return res
		}

		if reply.Header.Get(headerStatus) == statusError {
			res.ErrorClass = reply.Header.Get(headerErrorClass)
			res.ErrorCode = reply.Header.Get(headerErrorCode)
			res.Error = handlerMessage(reply.Data)
			if res.ErrorCode == codeEmbeddingUnavailable {
				res.Status, res.FailureKind = statusMiss, ""
				return res
			}
			res.FailureKind = failureHandler
			if res.ErrorClass == classTransient && retry() {
				continue
			}
			return res
		}
		res.ErrorClass, res.ErrorCode, res.Error = "", "", ""

		if len(reply.Data) > maxReplyBytes {
			res.FailureKind = failureOversized
			res.Error = fmt.Sprintf("reply of %d bytes exceeds %d", len(reply.Data), maxReplyBytes)
			return res
		}
		var resp similarResponse
		if err := json.Unmarshal(reply.Data, &resp); err != nil {
			res.FailureKind, res.Error = failureParse, err.Error()
			return res
		}
		if resp.EntityID != id {
			res.FailureKind = failureIDMismatch
			res.Error = fmt.Sprintf("reply entity_id %q for request %q", resp.EntityID, id)
			return res
		}
		if resp.Similar != nil {
			res.Similar = resp.Similar
		}
		res.ServerDuration = resp.Duration
		res.Directed = directedNeighbors(id, res.Similar, threshold, k)
		res.Status, res.FailureKind = statusOK, ""
		return res
	}
}

// handlerMessage extracts the ADR-060 {message} envelope, falling back to a
// bounded raw body.
func handlerMessage(data []byte) string {
	var body struct {
		Message string `json:"message"`
	}
	if err := json.Unmarshal(data, &body); err == nil && body.Message != "" {
		return body.Message
	}
	if len(data) > 512 {
		data = data[:512]
	}
	return string(data)
}

// sweep queries every id with bounded concurrency. Results are indexed like
// ids; entities never dispatched keep status not_queried. It returns a
// non-nil abort error once failures exceed abortFailureFraction of
// budgetTotal, the legacy provider's ENTITY_STATES denominator.
func sweep(ctx context.Context, r requester, ids []string, budgetTotal, k int, threshold float64,
	timeout, backoff time.Duration, concurrency int, progress func(done int),
) ([]queryResult, error) {
	results := make([]queryResult, len(ids))
	for i, id := range ids {
		results[i] = queryResult{EntityID: id, Status: statusNotQueried, Similar: []neighbor{}, Directed: []neighbor{}}
	}

	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	errBudget := errors.New("failure budget exhausted")

	var failed, done atomic.Int64
	jobs := make(chan int)
	var wg sync.WaitGroup
	for range concurrency {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				if ctx.Err() != nil {
					continue // drain: an aborted sweep leaves the rest not_queried
				}
				res := querySimilar(ctx, r, ids[i], k, threshold, timeout, backoff)
				results[i] = res
				if res.Status == statusFailed && res.FailureKind != failureCancelled {
					if n := failed.Add(1); overFailureBudget(int(n), budgetTotal) {
						cancel(fmt.Errorf("%w: %d failures exceed %.0f%% of %d entities",
							errBudget, n, 100*abortFailureFraction, budgetTotal))
					}
				}
				if n := int(done.Add(1)); progress != nil {
					progress(n)
				}
			}
		}()
	}
feed:
	for i := range ids {
		select {
		case jobs <- i:
		case <-ctx.Done():
			break feed
		}
	}
	close(jobs)
	wg.Wait()

	if cause := context.Cause(ctx); cause != nil && errors.Is(cause, errBudget) {
		return results, cause
	}
	return results, nil
}

// overFailureBudget is the legacy overCoverageThreshold: failed/total strictly
// above abortFailureFraction, never for an empty denominator.
func overFailureBudget(failed, total int) bool {
	return failed > 0 && total > 0 && float64(failed)/float64(total) > abortFailureFraction
}

// listKeys returns the deduplicated keys of a KV bucket, failing rather than
// truncating when the bucket holds more than maxKeys or ctx ends.
func listKeys(ctx context.Context, kv jetstream.KeyValue, maxKeys int) ([]string, error) {
	lister, err := kv.ListKeys(ctx)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lister.Stop() }()
	seen := make(map[string]bool)
	keys := []string{}
	for {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case key, ok := <-lister.Keys():
			if !ok {
				// jetstream closes the channel when ctx ends as well as when the
				// listing completes; only the latter is a full key set.
				if err := ctx.Err(); err != nil {
					return nil, err
				}
				return keys, nil
			}
			if seen[key] {
				continue
			}
			seen[key] = true
			if len(keys) >= maxKeys {
				return nil, fmt.Errorf("bucket %s holds more than -max-entities=%d keys", kv.Bucket(), maxKeys)
			}
			keys = append(keys, key)
		}
	}
}

type outgoingEntry struct {
	ToEntityID string `json:"to_entity_id"`
	Predicate  string `json:"predicate"`
}

// kvGetter is the subset of jetstream.KeyValue used for explicit-edge reads.
type kvGetter interface {
	Get(ctx context.Context, key string) (jetstream.KeyValueEntry, error)
}

// loadOutgoing reads the OUTGOING_INDEX row (a JSON array of
// {to_entity_id, predicate}, keyed by source entity ID) for each id with
// bounded concurrency. A missing key is an entity with no outgoing edges.
// Per-id read, decode or row-shape failures are returned in errsByID.
func loadOutgoing(ctx context.Context, kv kvGetter, ids []string, concurrency int,
) (out map[string]map[string]bool, errsByID map[string]string) {
	out = make(map[string]map[string]bool, len(ids))
	errsByID = make(map[string]string)
	var mu sync.Mutex
	jobs := make(chan string)
	var wg sync.WaitGroup
	for range concurrency {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for id := range jobs {
				if ctx.Err() != nil {
					continue
				}
				set, err := readOutgoing(ctx, kv, id)
				mu.Lock()
				if err != nil {
					errsByID[id] = err.Error()
				} else {
					out[id] = set
				}
				mu.Unlock()
			}
		}()
	}
feed:
	for _, id := range ids {
		select {
		case jobs <- id:
		case <-ctx.Done():
			break feed
		}
	}
	close(jobs)
	wg.Wait()
	for _, id := range ids {
		if _, ok := out[id]; !ok {
			if _, failed := errsByID[id]; !failed {
				reason := "not read"
				if cause := context.Cause(ctx); cause != nil {
					reason += ": " + cause.Error()
				}
				errsByID[id] = reason
			}
		}
	}
	return out, errsByID
}

func readOutgoing(ctx context.Context, kv kvGetter, id string) (map[string]bool, error) {
	entry, err := kv.Get(ctx, id)
	if err != nil {
		if errors.Is(err, jetstream.ErrKeyNotFound) {
			return map[string]bool{}, nil
		}
		return nil, err
	}
	if len(entry.Value()) > maxOutgoingValueBytes {
		return nil, fmt.Errorf("OUTGOING_INDEX value of %d bytes exceeds %d", len(entry.Value()), maxOutgoingValueBytes)
	}
	var entries []outgoingEntry
	if err := json.Unmarshal(entry.Value(), &entries); err != nil {
		return nil, fmt.Errorf("decode OUTGOING_INDEX row: %w", err)
	}
	set := make(map[string]bool, len(entries))
	for i, e := range entries {
		// As legacy getNeighborsFromBucket: an entry without a target poisons
		// the whole row rather than being skipped.
		if e.ToEntityID == "" {
			return nil, fmt.Errorf("OUTGOING_INDEX row entry %d is missing to_entity_id", i)
		}
		set[e.ToEntityID] = true
	}
	return set, nil
}
