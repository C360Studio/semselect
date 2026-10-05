// Package retry provides simple exponential backoff retry logic for the framework
package retry

import (
	"context"
	"errors"
	"fmt"
	"math/rand"
	"sync"
	"time"
)

var (
	// Thread-safe random source for jitter
	randMu     sync.Mutex
	randSource = rand.New(rand.NewSource(time.Now().UnixNano()))
)

// NonRetryableError wraps errors that should not be retried
type NonRetryableError struct {
	Err error
}

func (e *NonRetryableError) Error() string {
	return fmt.Sprintf("non-retryable: %v", e.Err)
}

func (e *NonRetryableError) Unwrap() error {
	return e.Err
}

// NonRetryable wraps an error to indicate it should not be retried
func NonRetryable(err error) error {
	if err == nil {
		return nil
	}
	return &NonRetryableError{Err: err}
}

// IsNonRetryable checks if an error is marked as non-retryable
func IsNonRetryable(err error) bool {
	var nre *NonRetryableError
	return errors.As(err, &nre)
}

// Config provides retry configuration
type Config struct {
	MaxAttempts  int           // Maximum number of attempts (0 = no retry, just run once)
	InitialDelay time.Duration // Initial delay between attempts
	MaxDelay     time.Duration // Maximum delay between attempts
	Multiplier   float64       // Backoff multiplier (typically 2.0)
	AddJitter    bool          // Add randomness to prevent thundering herd
}

// DefaultConfig returns sensible defaults for retry operations
func DefaultConfig() Config {
	return Config{
		MaxAttempts:  3,
		InitialDelay: 100 * time.Millisecond,
		MaxDelay:     5 * time.Second,
		Multiplier:   2.0,
		AddJitter:    true,
	}
}

// Do executes fn with exponential backoff retry
func Do(ctx context.Context, cfg Config, fn func() error) error {
	// Validate configuration
	if cfg.InitialDelay < 0 {
		return errors.New("retry: InitialDelay cannot be negative")
	}
	if cfg.MaxDelay < 0 {
		return errors.New("retry: MaxDelay cannot be negative")
	}
	if cfg.Multiplier < 0 {
		return errors.New("retry: Multiplier cannot be negative")
	}
	// Prevent overflow with extremely large multipliers
	if cfg.Multiplier > 1000 {
		cfg.Multiplier = 1000
	}

	if cfg.MaxAttempts <= 0 {
		cfg.MaxAttempts = 1 // At least try once
	}

	// Set defaults if not specified
	if cfg.InitialDelay == 0 {
		cfg.InitialDelay = 100 * time.Millisecond
	}
	if cfg.MaxDelay == 0 {
		cfg.MaxDelay = 5 * time.Second
	}
	if cfg.Multiplier == 0 {
		cfg.Multiplier = 2.0
	}

	// Additional validation after defaults
	if cfg.MaxDelay > 0 && cfg.MaxDelay < cfg.InitialDelay {
		return errors.New("retry: MaxDelay must be >= InitialDelay")
	}

	var lastErr error
	delay := cfg.InitialDelay

	for attempt := 1; attempt <= cfg.MaxAttempts; attempt++ {
		// Try the operation
		err := fn()
		if err == nil {
			return nil // Success!
		}
		lastErr = err

		// Check if error is marked as non-retryable - fail immediately
		if IsNonRetryable(err) {
			return err
		}

		// Check if context is cancelled
		if ctx.Err() != nil {
			return fmt.Errorf("retry cancelled before attempt %d: %w", attempt, ctx.Err())
		}

		// Don't sleep after the last attempt
		if attempt == cfg.MaxAttempts {
			break
		}

		// Calculate sleep duration with optional jitter
		sleepDuration := delay
		if cfg.AddJitter {
			// Add up to 25% jitter using thread-safe random
			randMu.Lock()
			jitter := time.Duration(randSource.Int63n(int64(delay / 4)))
			randMu.Unlock()
			sleepDuration = delay + jitter
		}

		// Sleep with context cancellation support
		timer := time.NewTimer(sleepDuration)
		select {
		case <-ctx.Done():
			timer.Stop() // Stop timer immediately when context cancelled
			return fmt.Errorf("retry cancelled during backoff for attempt %d: %w", attempt+1, ctx.Err())
		case <-timer.C:
			// Timer fired, channel drained, no need to stop
		}

		// Calculate next delay with overflow protection
		nextDelay := float64(delay) * cfg.Multiplier
		// Check for overflow or exceeding MaxDelay
		if nextDelay > float64(cfg.MaxDelay) || nextDelay > float64(time.Duration(1<<63-1)) {
			delay = cfg.MaxDelay
		} else {
			delay = time.Duration(nextDelay)
		}
	}

	return fmt.Errorf("retry failed after %d attempts: %w", cfg.MaxAttempts, lastErr)
}

// DoWithResult executes fn with retry and returns both result and error
func DoWithResult[T any](ctx context.Context, cfg Config, fn func() (T, error)) (T, error) {
	var result T
	err := Do(ctx, cfg, func() error {
		var innerErr error
		result, innerErr = fn()
		return innerErr
	})
	return result, err
}

// Quick returns a config for fast retries (useful during startup)
func Quick() Config {
	return Config{
		MaxAttempts:  10,
		InitialDelay: 50 * time.Millisecond,
		MaxDelay:     1 * time.Second,
		Multiplier:   1.5,
		AddJitter:    true,
	}
}

// Persistent returns a config for long-running retries (useful for critical resources)
func Persistent() Config {
	return Config{
		MaxAttempts:  30,
		InitialDelay: 200 * time.Millisecond,
		MaxDelay:     10 * time.Second,
		Multiplier:   2.0,
		AddJitter:    true,
	}
}
