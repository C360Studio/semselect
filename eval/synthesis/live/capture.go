//go:build ignore

// Command capture preserves one request per planned query against owned NATS.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

func writeJSON(path string, value any) error {
	b, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(b, '\n'), 0600)
}

func hash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

func main() {
	url := flag.String("url", "nats://nats:4222", "owned NATS endpoint")
	mode := flag.String("mode", "capture", "snapshot or capture")
	plan := flag.String("plan", "/plan/query-plan.json", "frozen query plan")
	out := flag.String("out", "/capture", "output directory, or snapshot filename")
	flag.Parse()
	nc, err := nats.Connect(*url, nats.Name("semselect-synthesis-acquisition"), nats.Timeout(5*time.Second), nats.NoReconnect())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer nc.Close()
	if *mode == "snapshot" {
		err = snapshot(nc, *out)
	} else if *mode == "capture" {
		err = capture(nc, *plan, *out)
	} else {
		err = fmt.Errorf("unknown mode %q", *mode)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func snapshot(nc *nats.Conn, out string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}
	buckets := make(map[string]any)
	for _, name := range []string{"GRAPH_STATUS", "COMMUNITY_INDEX", "COMMUNITY_SUMMARIES"} {
		kv, err := js.KeyValue(ctx, name)
		if err != nil {
			buckets[name] = map[string]string{"error": err.Error()}
			continue
		}
		keys, err := kv.ListKeys(ctx)
		if err != nil {
			buckets[name] = map[string]string{"error": err.Error()}
			continue
		}
		records := make(map[string]any)
		for key := range keys.Keys() {
			entry, err := kv.Get(ctx, key)
			if err != nil {
				records[key] = map[string]string{"error": err.Error()}
				continue
			}
			var value any = string(entry.Value())
			if json.Valid(entry.Value()) {
				value = json.RawMessage(entry.Value())
			}
			records[key] = map[string]any{"revision": entry.Revision(), "created": entry.Created(), "value": value, "sha256": hash(entry.Value())}
		}
		keys.Stop()
		buckets[name] = records
	}
	return writeJSON(out, map[string]any{"captured_utc": time.Now().UTC(), "buckets": buckets})
}

func capture(nc *nats.Conn, planPath, out string) error {
	data, err := os.ReadFile(planPath)
	if err != nil {
		return err
	}
	var plan struct {
		Queries []struct {
			ID    string `json:"id"`
			Query string `json:"query"`
		} `json:"queries"`
	}
	if err := json.Unmarshal(data, &plan); err != nil {
		return err
	}
	if len(plan.Queries) != 13 {
		return fmt.Errorf("expected frozen 13 queries, got %d", len(plan.Queries))
	}
	if err := os.MkdirAll(filepath.Join(out, "raw"), 0700); err != nil {
		return err
	}
	inputs, err := os.OpenFile(filepath.Join(out, "inputs.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer inputs.Close()
	statuses, err := os.OpenFile(filepath.Join(out, "status.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer statuses.Close()
	for _, query := range plan.Queries {
		if query.ID == "" || filepath.Base(query.ID) != query.ID {
			return fmt.Errorf("invalid ID %q", query.ID)
		}
		request, err := json.Marshal(map[string]any{"query": query.Query, "summarize_threshold": 1})
		if err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(out, "raw", query.ID+".request.json"), request, 0600); err != nil {
			return err
		}
		started := time.Now()
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		response, requestErr := nc.RequestWithContext(ctx, "graph.query.searchGraph", request)
		cancel()
		status := map[string]any{"id": query.ID, "query": query.Query, "started_utc": started.UTC(), "elapsed_seconds": time.Since(started).Seconds(), "subject": "graph.query.searchGraph", "request_sha256": hash(request), "timeout_seconds": 60, "attempts": 1}
		summaries := []json.RawMessage{}
		count := 0
		if requestErr != nil {
			status["status"] = "request_error"
			status["error"] = requestErr.Error()
		} else {
			if err := os.WriteFile(filepath.Join(out, "raw", query.ID+".response.json"), response.Data, 0600); err != nil {
				return err
			}
			status["response_bytes"] = len(response.Data)
			status["response_sha256"] = hash(response.Data)
			var body struct {
				Summaries []json.RawMessage `json:"community_summaries"`
				Count     *int              `json:"count"`
				Model     string            `json:"answer_model"`
				Degraded  bool              `json:"degraded"`
				Reason    string            `json:"degraded_reason"`
			}
			if err := json.Unmarshal(response.Data, &body); err != nil || body.Count == nil {
				status["status"] = "invalid_response"
				status["error"] = "Response must be valid JSON with a count field; full body preserved."
			} else {
				count = *body.Count
				if body.Summaries != nil {
					summaries = body.Summaries
				}
				status["status"] = "captured"
				if len(summaries) == 0 {
					status["status"] = "empty_summaries"
				}
				status["answer_model"] = body.Model
				status["degraded"] = body.Degraded
				status["degraded_reason"] = body.Reason
			}
		}
		status["summary_count"] = len(summaries)
		status["total_entities"] = count
		input := map[string]any{"id": query.ID, "query": query.Query, "summaries": summaries, "total_entities": count}
		if err := json.NewEncoder(inputs).Encode(input); err != nil {
			return err
		}
		if err := inputs.Sync(); err != nil {
			return err
		}
		if err := json.NewEncoder(statuses).Encode(status); err != nil {
			return err
		}
		if err := statuses.Sync(); err != nil {
			return err
		}
		fmt.Printf("%s %s summaries=%d entities=%d elapsed=%.3fs\n", query.ID, status["status"], len(summaries), count, time.Since(started).Seconds())
	}
	return writeJSON(filepath.Join(out, "capture.json"), map[string]any{"completed_utc": time.Now().UTC(), "query_plan_sha256": hash(data), "planned": len(plan.Queries), "requests_per_query": 1, "scope": "Acquisition only; answers are preserved but not used for selection or formal metrics."})
}
