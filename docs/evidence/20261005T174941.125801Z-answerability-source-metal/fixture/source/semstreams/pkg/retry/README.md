# Retry Package

Simple exponential backoff retry for the SemStreams framework.

## Overview

This package provides a minimal retry mechanism with exponential backoff. It's designed to be simple, predictable, and actually used throughout the framework.

## Quick Start

```go
import "github.com/c360/semstreams/pkg/retry"

// Use defaults (3 attempts, 100ms initial delay)
err := retry.Do(ctx, retry.DefaultConfig(), func() error {
    return someOperation()
})

// Or configure as needed
cfg := retry.Config{
    MaxAttempts:  5,
    InitialDelay: 200 * time.Millisecond,
    MaxDelay:     10 * time.Second,
    Multiplier:   2.0,
    AddJitter:    true,
}
err := retry.Do(ctx, cfg, func() error {
    return someOperation()
})
```

## Framework Patterns

### Pattern 1: KV Bucket Access

Use retry when accessing KV buckets that might not exist yet:

```go
cfg := retry.Quick() // 10 attempts, fast retries
bucket, err := retry.DoWithResult(ctx, cfg, func() (jetstream.KeyValue, error) {
    return js.KeyValue(ctx, bucketName)
})
```

Or better, use `CreateKeyValueBucket` which handles this internally:

```go
bucket, err := natsClient.CreateKeyValueBucket(ctx, kvConfig)
```

### Pattern 2: Component Initialization

Components should retry connecting to dependencies during startup:

```go
func (c *Component) Start(ctx context.Context) error {
    cfg := retry.Persistent() // 30 attempts, longer delays

    return retry.Do(ctx, cfg, func() error {
        // Try to connect to required services
        if err := c.connectToNATS(); err != nil {
            return err
        }
        if err := c.initializeKVBuckets(); err != nil {
            return err
        }
        return nil
    })
}
```

### Pattern 3: Network Operations

Always retry network operations:

```go
func (c *Client) SendMessage(msg *Message) error {
    cfg := retry.DefaultConfig() // 3 attempts, standard backoff

    return retry.Do(ctx, cfg, func() error {
        return c.conn.Send(msg)
    })
}
```

## Configuration

### Pre-defined Configs

- **DefaultConfig()**: 3 attempts, 100ms-5s delay, 2x multiplier
- **Quick()**: 10 attempts, 50ms-1s delay, 1.5x multiplier (for startup)
- **Persistent()**: 30 attempts, 200ms-10s delay, 2x multiplier (for critical resources)

### Custom Config

```go
cfg := retry.Config{
    MaxAttempts:  5,           // Number of tries
    InitialDelay: time.Second, // First retry delay
    MaxDelay:     time.Minute, // Maximum retry delay
    Multiplier:   3.0,         // Delay multiplier
    AddJitter:    true,        // Add randomness
}
```

## How It Works

1. Executes your function
2. On success, returns immediately
3. On failure, waits with exponential backoff
4. Continues until success or max attempts reached
5. Respects context cancellation at all times

Example timing with default config:
- Attempt 1: immediate
- Attempt 2: wait 100ms
- Attempt 3: wait 200ms
- Total time: ~300ms + execution time

## Best Practices

✅ **DO**:
- Use `Quick()` during component startup
- Use `Persistent()` for critical resources
- Use `DefaultConfig()` for normal operations
- Always pass a context for cancellation
- Use `CreateKeyValueBucket()` for KV buckets

❌ **DON'T**:
- Retry non-transient errors (e.g., invalid input)
- Use excessive attempts (>30)
- Use very short delays (<50ms)
- Ignore context cancellation
- Add retry to already-resilient operations

## Why This Design?

- **Simple**: ~150 lines total vs 850+ lines before
- **Focused**: Just exponential backoff, no circuit breakers or metrics
- **Used**: Patterns that components actually need
- **Predictable**: Clear timing, optional jitter
- **Compatible**: Works with existing code