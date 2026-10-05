# Cache Package

A high-performance, thread-safe caching library for Go with multiple eviction policies, built-in statistics, and optional Prometheus metrics integration.

## Features

- 🚀 **High Performance**: Optimized for concurrent access with minimal lock contention
- 📊 **Always Observable**: Statistics always enabled (never operate in the dark)
- 📈 **Prometheus Ready**: Optional metrics integration for production monitoring
- 🔧 **Flexible Eviction**: LRU, TTL, Hybrid, or no eviction policies
- 🎯 **Type-Safe**: Full generic support for any value type
- 🧩 **Functional Options**: Clean, composable configuration API

## Installation

```go
import "github.com/c360/semstreams/pkg/cache"
```

## Quick Start

### Basic Usage

```go
// Simple cache with default settings (stats always enabled)
c := cache.NewSimple[string]()

// LRU cache with max 1000 items
c := cache.NewLRU[*MyStruct](1000)

// TTL cache with 5-minute expiry and 1-minute cleanup interval
c := cache.NewTTL[int](ctx, 5*time.Minute, 1*time.Minute)

// Hybrid cache combining LRU and TTL
c := cache.NewHybrid[string](ctx, 1000, 5*time.Minute, 1*time.Minute)
```

### With Prometheus Metrics

```go
import "github.com/c360/semstreams/pkg/metric"

// Create metrics registry
registry := metric.NewMetricsRegistry()

// Create cache with metrics export
c := cache.NewLRU[*Entity](1000,
    cache.WithMetrics[*Entity](registry, "my_component"),
)

// Metrics automatically exported:
// - semstreams_cache_hits_total{component="my_component"}
// - semstreams_cache_misses_total{component="my_component"}
// - semstreams_cache_size{component="my_component"}
// - etc.
```

### With Multiple Options

```go
// Compose multiple functional options
c := cache.NewTTL[*Document](ctx, 10*time.Minute, 1*time.Minute,
    cache.WithMetrics[*Document](registry, "document_cache"),
    cache.WithEvictionCallback[*Document](func(key string, value *Document) {
        log.Printf("Evicted document: %s", key)
    }),
    cache.WithStatsInterval[*Document](30*time.Second),
)
```

## Cache Types

### Simple Cache

No eviction policy - items remain until explicitly deleted.

```go
c := cache.NewSimple[V]()
```

### LRU Cache

Evicts least recently used items when capacity is reached.

```go
c := cache.NewLRU[V](maxSize)
```

### TTL Cache

Evicts items after a time-to-live period expires.

```go
c := cache.NewTTL[V](ctx, ttl, cleanupInterval)
```

### Hybrid Cache

Combines LRU and TTL - evicts items that are either expired or least recently used.

```go
c := cache.NewHybrid[V](ctx, maxSize, ttl, cleanupInterval)
```

## Functional Options

The cache package uses functional options for clean, composable configuration:

### WithMetrics

Enable Prometheus metrics export:

```go
cache.WithMetrics[V](registry, "component_name")
```

### WithEvictionCallback

Set a callback for when items are evicted:

```go
cache.WithEvictionCallback[V](func(key string, value V) {
    // Handle evicted item
})
```

### WithStatsInterval

Set statistics aggregation interval (TTL/Hybrid caches only):

```go
cache.WithStatsInterval[V](30*time.Second)
```

## API Reference

### Cache Interface

```go
type Cache[V any] interface {
    Get(key string) (V, bool)       // Retrieve value by key
    Set(key string, value V) bool   // Store key-value pair
    Delete(key string) bool          // Remove entry by key
    Clear()                          // Remove all entries
    Size() int                       // Current number of entries
    Keys() []string                  // All keys currently in cache
    Stats() *Statistics              // Cache statistics (never nil)
}
```

### Statistics

Statistics are **always** collected (not optional) for observability:

```go
stats := cache.Stats()

// Available metrics:
stats.Hits()              // Total cache hits
stats.Misses()            // Total cache misses  
stats.HitRatio()          // Hit rate (0.0 to 1.0)
stats.RequestsPerSecond() // Throughput
stats.CurrentSize()       // Current entries
stats.Evictions()         // Total evictions
```

## Prometheus Metrics

When enabled via `WithMetrics()`, the following metrics are exported:

| Metric | Type | Description |
|--------|------|-------------|
| `semstreams_cache_hits_total` | Counter | Total cache hits |
| `semstreams_cache_misses_total` | Counter | Total cache misses |
| `semstreams_cache_sets_total` | Counter | Total set operations |
| `semstreams_cache_deletes_total` | Counter | Total delete operations |
| `semstreams_cache_evictions_total` | Counter | Total evictions |
| `semstreams_cache_size` | Gauge | Current number of entries |

All metrics include a `component` label for identifying different cache instances.

## Configuration-Driven Cache Creation

The `NewFromConfig()` function enables cache creation from configuration files (YAML/JSON):

```go
// Load from config file
config := cache.Config{
    Enabled:         true,
    Strategy:        cache.StrategyLRU,
    MaxSize:         1000,
    TTL:             5 * time.Minute,
    CleanupInterval: 1 * time.Minute,
    StatsInterval:   30 * time.Second,
}

// Create cache from config with optional functional options
c, err := cache.NewFromConfig[V](ctx, config,
    cache.WithMetrics[V](registry, "component_name"),
    cache.WithEvictionCallback[V](func(key string, value V) {
        log.Printf("Evicted: %s", key)
    }),
)
```

This pattern is useful for:

- Runtime cache strategy selection
- Deployment-specific tuning without code changes
- Configuration files (YAML/JSON) that specify cache behavior

## Performance

Benchmark results on MacBook Pro M3:

```text
BenchmarkCacheGet/Simple-12         6,846,386    172.8 ns/op
BenchmarkCacheGet/LRU_1000-12       5,310,026    226.6 ns/op
BenchmarkCacheGet/TTL-12            5,605,714    213.5 ns/op
BenchmarkCacheGet/Hybrid_1000-12    4,665,702    257.2 ns/op

BenchmarkCacheSet/Simple-12         4,666,502    256.8 ns/op
BenchmarkCacheSet/LRU_1000-12       3,477,819    361.4 ns/op
BenchmarkCacheSet/TTL-12            3,702,312    324.1 ns/op
BenchmarkCacheSet/Hybrid_1000-12    3,161,434    379.3 ns/op
```

### Performance Tips

1. **Metrics Overhead**: ~5% when enabled, zero when disabled
2. **Stats Overhead**: Negligible (atomic operations)
3. **Lock Contention**: Use multiple cache instances for high-concurrency scenarios
4. **Memory**: Consider item size when setting max capacity

## Architecture

### Observability: Dual Tracking Pattern

The cache package tracks operations through two independent systems:

```mermaid
flowchart LR
    A[Cache Operation] --> B[Statistics]
    A --> C[Metrics]

    B --> D[Atomic Counters]
    B --> E[Computed Values]

    C --> F[Prometheus Counters]
    C --> G[Prometheus Gauges]

    D --> H[cache.Stats API]
    E --> H

    F --> I[/metrics endpoint]
    G --> I

    style A fill:#e1f5ff
    style B fill:#d4edda
    style C fill:#fff3cd
    style H fill:#d4edda
    style I fill:#fff3cd
```

**Why Track Twice?**

Both Statistics and Metrics independently track operations, which appears redundant but serves distinct purposes:

| Aspect | Statistics (Always On) | Metrics (Optional) |
|--------|------------------------|-------------------|
| **Purpose** | Local debugging & programmatic access | Time-series monitoring & dashboards |
| **Dependency** | None (atomic operations) | Prometheus registry |
| **Computed Values** | Hit ratio, requests/sec | Raw counters/gauges only |
| **Access** | `cache.Stats()` API | `/metrics` HTTP endpoint |
| **Overhead** | ~50ns/op | ~50ns/op (when enabled) |
| **Use Case** | Tests, debugging, runtime inspection | Production dashboards, alerting |

**Performance Trade-off:**

- Dual tracking overhead: **~5% per operation** when metrics enabled
- At 1M ops/sec: **0.5-1% total overhead**
- Negligible cost for comprehensive observability

**Alternative Considered:** Reading Statistics from Prometheus metrics to avoid duplication.

**Rejected because:**

- Creates Prometheus dependency for basic stats
- 10x slower (reading from Prometheus vs atomic operations)
- Breaks Statistics when metrics disabled
- Violates separation of concerns

### Cache Architecture by Type

```mermaid
flowchart TB
    subgraph Simple["Simple Cache (No Eviction)"]
        S1[Map: key → value]
    end

    subgraph LRU["LRU Cache (Capacity-Based)"]
        L1[Map: key → list element]
        L2[Doubly-Linked List]
        L1 --> L2
        L2 -.->|"Move to front on access"| L2
        L2 -.->|"Evict tail when full"| L2
    end

    subgraph TTL["TTL Cache (Time-Based)"]
        T1[Map: key → entry]
        T2[Expiry: key → timestamp]
        T3[Cleanup Goroutine]
        T1 --> T2
        T3 -.->|"Periodic scan"| T2
    end

    subgraph Hybrid["Hybrid Cache (Capacity + Time)"]
        H1[Map: key → list element]
        H2[Doubly-Linked List]
        H3[Expiry: key → timestamp]
        H4[Cleanup Goroutine]
        H1 --> H2
        H1 --> H3
        H4 -.->|"Periodic scan"| H3
        H2 -.->|"LRU eviction"| H2
    end

    style Simple fill:#e1f5ff
    style LRU fill:#d4edda
    style TTL fill:#fff3cd
    style Hybrid fill:#f8d7da
```

### Eviction Policy Flow

```mermaid
stateDiagram-v2
    [*] --> CheckCapacity: Set(key, value)

    CheckCapacity --> Simple: Simple Cache
    CheckCapacity --> CheckLRU: LRU/Hybrid
    CheckCapacity --> CheckTTL: TTL/Hybrid

    Simple --> Store: Always store

    CheckLRU --> EvictLRU: At capacity
    CheckLRU --> Store: Below capacity
    EvictLRU --> Store: Remove LRU item

    CheckTTL --> Store: Not expired
    CheckTTL --> Replace: Expired
    Replace --> Store: Remove expired item

    Store --> [*]

    note right of EvictLRU
        Remove least recently
        used item from tail
    end note

    note right of CheckTTL
        Background cleanup
        removes expired items
    end note
```

### Architecture Decisions

#### Why Stats Are Always On

Statistics collection is mandatory because:

- **Observability is critical** for production systems
- **Negligible overhead** (atomic operations ~50ns)
- **Debugging without stats** is nearly impossible
- **Hit ratios** inform capacity planning and eviction policy tuning
- **No external dependencies** required for basic monitoring

#### Why Functional Options

We chose functional options over struct-based configuration because:

- **More idiomatic Go** pattern
- **Composable and extensible** - easy to add features
- **Clear intent** with named functions
- **No zero-value confusion** in configuration
- **Backward compatible** when adding new options

#### Why Multiple Cache Types

Different eviction strategies serve different use cases:

- **Simple**: Explicit control, no automatic eviction
- **LRU**: Access pattern optimization, fixed capacity
- **TTL**: Time-sensitive data, automatic expiration
- **Hybrid**: Production-grade caching with both limits

## Examples

### Production Cache with Full Monitoring

```go
func setupProductionCache(ctx context.Context, registry *metric.MetricsRegistry) cache.Cache[*User] {
    return cache.NewHybrid[*User](
        ctx,
        10000,                // Max 10k users
        30*time.Minute,       // 30 min TTL
        5*time.Minute,        // Cleanup every 5 min
        cache.WithMetrics[*User](registry, "user_cache"),
        cache.WithEvictionCallback[*User](func(key string, user *User) {
            log.Printf("Evicted user from cache: %s", user.ID)
        }),
    )
}
```

### Request-Scoped Cache

```go
func handleRequest(ctx context.Context) {
    // Create a request-scoped cache
    requestCache := cache.NewLRU[*ComputedResult](100)
    defer requestCache.Clear()
    
    // Use cache during request processing
    if result, ok := requestCache.Get(key); ok {
        return result
    }
    
    // Compute and cache
    result := expensiveComputation()
    requestCache.Set(key, result)
}
```

## Thread Safety

All cache operations are thread-safe. The implementation uses:

- Fine-grained locking with `sync.RWMutex`
- Atomic operations for statistics
- Lock-free reads where possible

## Contributing

When adding new cache implementations:

1. Statistics must always be initialized
2. Follow functional options pattern
3. Support optional Prometheus metrics
4. Maintain thread safety
5. Include comprehensive tests with race detection

## License

See LICENSE file in repository root.
