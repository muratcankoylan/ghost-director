# Contributing

Start with the README and architecture. Keep changes incremental, preserve existing productions and record changed invariants in tests. A model response is a proposal; validate it before changing shared state.

Run backend tests, frontend tests and the production build. Add focused tests for boundary/failure behavior rather than copying implementation detail. Never add private production files, credentials, provider payloads or an unreviewed asset bundle to a pull request.

For provider changes, verify the official contract and date the evidence. Report costs before live tests; never enable paid CI or silently switch a model. UI labels may be capability-focused, but provenance must identify the actual service and model.

Pull requests should state the concrete problem, resulting behavior, executed verification and remaining limitations. Clearly identify work that requires Blender, external Studio OS context or a separately approved paid experiment.
