# Caller binder and bounded code baseline

Implemented 2026-10-07 inside this evaluation only. The author checked this
implementation; this file does not claim independent approval. No sibling or
production service was edited. No held-out text, gold, or predictions were read
by the binder/rule implementer. The separate fixture owner supplied development
cases and agreed the binding contract before validation.

## Scope and contract

`binder.bind(query, operation, catalog)` binds case-sensitive literal values from
the supplied `nodes`, `zones`, or `fields` arrays. A value must be a complete
identifier, not a substring of a longer ID. A terminal period is punctuation,
but a dotted suffix remains part of an ID. Repeated occurrences of the same
literal are one binding; multiple distinct relevant literals require binding.
Quoted literals are valid identifiers. The binder does not interpret negation,
resolve pronouns, guess entities, copy example arguments, or prioritize the
first occurrence. Operation selection owns the language judgment.

`no_override` produces empty options and is not executable. Similarity and count
need no argument under the inherited hint contract. Path requires one `nodes`
literal; zone requires one `zones` literal and adds `located_in`; numerical
aggregations require one `fields` literal. A missing/ambiguous literal preserves
only base intent/type options and returns `needs_binding`, executable false.
Catalog membership is not authorization or proof of real entity existence.

The improved rules are an evaluation-local lexical grammar, completed within
the one-engineer-day allowance. Changes are limited to common operation words,
development-observed spelling variants, literal location phrases, clause-level
negation, quoted operation mentions, unsupported operations, and attempts to
override classification instructions. There is no learned component, parser
framework, entity dictionary, fuzzy entity matching, or generated confidence.
Negation removes the remaining clause until punctuation or a contrast marker;
quoted text is removed from operation detection. An affirmative supported
request can survive a separate negated/quoted mention. Multiple affirmative
operations, unsupported requests, or no operation produce `no_override`.

This is a bounded grammar, not complete natural-language understanding. It can
misread complicated negation scope, noun phrases using operation words, unfamiliar
paraphrases, spelling changes outside its fixed lexicon, or implicit locations.
Development fit is tuning evidence, not proof those limitations are solved.

## Actual reused code

`baselines.classify_code` invokes the unchanged existing
`../query-routing/driver` binary, importing SemStreams `v1.0.0-beta.160`, and
loads the exact `../query-routing/training.json`. Existing module/source pins
remain at `../query-routing/driver/source-pins.json`. It does not copy the Go
implementation into Python or label the historical pin current production.
The execution freeze must bind the supplied binary hash and source pin hashes;
the bridge records binary, training, and input SHA-256 values.

Keyword and default BM25 use threshold 0.7; tuned BM25 receives the development
selected threshold. Each query gets a fresh process/classifier, so no earlier
query changes its BM25 corpus statistics. There is no per-query output cache.
Native SearchOptions, Intent, Confidence, tier, driver records, constructor and
classification timings remain separately visible. Caller binder outputs must
never replace the native evidence. The bridge decodes the operation only; it
does not repair copied arguments. Empty native options map to `no_override`
without pretending upstream emitted an abstention. Unsupported native options,
conflicting operations/intents, invalid output, nonzero exit, and timeout remain
errors. A timeout kills/reaps the subprocess and retains its synced partial
records before temporary files are removed.

Bridge elapsed time includes process setup and filesystem work, and is not an
HTTP service latency measurement. Consistent HTTP timing belongs to the outer
evaluation runner. Code confidence is never invented or used for acceptance;
native confidence is preserved only as evidence.

## Regression and validation record

Before fixes, the following behavior tests failed, then passed after the
corresponding changes:

- `test_literal_binding`: `Average temperature.` retained only
  `aggregation_type=avg` instead of also binding `temperature`. Boundary handling
  now distinguishes sentence punctuation from qualified identifier suffixes.
- `test_development_language_gaps`: 14 subcases failed, covering observed
  paraphrases/typos, location wording, composed count/location requests,
  unresolved similarity/path choice, elapsed-time arithmetic, and a system
  instruction override. Fixed lexical patterns are reviewable in `baselines.py`.
- `test_timeout_retains_partial_native_record`: timed-out driver evidence was
  discarded with the temporary directory (`native_options=None`); it now retains
  the already-written `path_intent` record while reporting an error.

Validation on Darwin/ARM64 used the cached pinned module, with no inference:

```sh
cd eval/query-routing/driver
GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOCACHE=/tmp/semselect-specialist-gocache go build -mod=readonly -o /tmp/semselect-specialist-query-driver .
GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOCACHE=/tmp/semselect-specialist-gocache go test -mod=readonly -race ./...
cd ../../..
SEMSELECT_QUERY_DRIVER=/tmp/semselect-specialist-query-driver python3 -m unittest discover -s eval/specialist-intent -p 'test_b*.py' -v
```

The actual Go driver race tests passed. The Python binder/bridge suite passed all
15 tests, including actual keyword and BM25 execution, with no skips when the
binary environment variable was supplied. Initial build printed a nonfatal
module-cache stat-write sandbox warning; build exited zero, and subsequent real
driver tests passed without modifying dependency files.

After development-only tuning, improved rules selected 60/60 development
operations correctly and binder outputs matched 60/60 complete gold results.
These authored cases informed the fixes, so this is not a held-out score, a
benchmark, or evidence of improvement over a specialist. No held-out comparison,
model accuracy, CPU/Linux runtime, or production suitability is established by
these checks.
