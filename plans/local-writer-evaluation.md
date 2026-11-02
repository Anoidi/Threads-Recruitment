# Local writer evaluation, 2026-10-09

## Live rewrite contract regression

## Live rewrite style regression

The first live probe had an awkward comma before the student rate. Added bounded
style rejection for that construction, «умова: умова» and «запитайте у приватні
повідомлення», plus native Ukrainian examples in the correction prompt. After
backend/worker redeploy, the same read-only source returned in 6.5 seconds with
`Для студентів до 26 років: 31,40 зл/год netto.`, exact `umowa zlecenie`, both
rates and `Пишіть у приватні повідомлення.`. No database write/publication.
This is a small regression set, not general native-language acceptance.

Current production writer route, read-only AIService.rewrite alternative/uk probe: 19.8 seconds. Both rates (23 and 31.40 PLN/hour), student age under 26 and netto survived, but umowa zlecenie was incorrectly translated to «умова залучення». This passed numeric/style gates and proved they were insufficient. Added exact contract-name presence preservation (also rejects invention), explicit rewrite instruction and bounded-correction regression. After redeployment the same source returned in 6.1 seconds with both rates, student condition, netto and umowa zlecenie intact. Wording still has an awkward comma after «Для студентів до 26 років» and is not an overall native-language acceptance. Neither probe saved or published a post. Full backend after change: 196 passed.

Read-only inference, no saved drafts/publications. Installed models only; no downloads or route changes. Hardware: T1200 4 GB. Sequential requests, num_ctx=4096, num_predict=200, temperature=0.3, seed=42, think=false.

## Polish schedule-advice case

Prompt: practical post under 400 characters on questions about a work schedule before accepting employment, no invented hours/contracts, no emoji/advertising.

- qwen3:4b-instruct: 14.0 seconds, finish=stop. Misinterpreted Polish 'grafik' as graphic designer and returned project/material/design questions. Semantic failure despite valid nonempty output.
- qwen3:4b: 20.2 seconds, finish=length. Returned English self-deliberation instead of final post despite think=false. Not suitable as a drop-in replacement with these settings.
- qwen3:4b-instruct with explicit warehouse recruitment context and 'harmonogram zmian': returned relevant schedule questions, but repetitive, unnatural Polish and no concise prioritized checklist. Clarifying ambiguity helped relevance, not enough for native editorial acceptance.

## Decision

Explicit full-language-name follow-up: Gemma Ukrainian case now used Ukrainian (8.9s/353 chars), but added a draft preamble, formal salutation and thanks. Polish case was relevant/grammatical enough for further editing but still 510 chars (9.8s) and generic framing. Neither meets final publication acceptance. Working writer/planner prompts now specify full language names, and validation rejects entirely non-Cyrillic uk/ru content. Full suite after the language-alphabet guard: 172 passed; subsequent prompt wording requires focused verification. Routes unchanged.

Gemma follow-up result: uk case took 46.3s/286 characters but returned Polish, so semantic/language acceptance failed even though mechanical gate initially passed. pl case took 13.4s/717 characters, failed the length gate and inserted arbitrary 8/12-hour examples. Benchmark now states language code explicitly and flags an all-Latin uk/ru response. No production model switch justified by this run.

Follow-up: Gemma 3 4B official Ollama package selected for an isolated comparison, not production replacement. Package listed around 3.4 GB at https://ollama.com/library/gemma3/tags. Host audit: about 5.4 GB free RAM and 417.5 GiB free disk. Pull started via local Ollama API; completion and RAM/latency/quality are not yet verified. scripts/evaluate_writer_quality.py provides sequential read-only uk/pl probes with fixed seed, 4096 context and no post/database writes.

Download follow-up: original pull session remains running; installed-tag list still lacks Gemma. Ollama partial metadata reports 297.9 MiB completed of a 2383.2 MiB layer. Preallocated blob file size is not download completion evidence. Evaluation script AST validated inside test container. No second pull or inference started while the first pull is active.

Evaluation harness now exits nonzero on request errors or mechanical failures (empty/overlong output, unfinished generation, visible reasoning, forbidden contacts, mixed Cyrillic in Polish). It still labels every shape-valid output as requiring native semantic review; passing this gate is not model-quality acceptance. Added regression tests for these checks. Download session remains live but observed layer completion has not increased at the latest check; no restart assumed from that observation alone.

Keep current routes unchanged. Neither installed model passes content quality acceptance. Do not substitute empty/schema-valid output for usable text. Further evaluation must compare a genuinely stronger multilingual model within the memory budget or a local native-language revision stage, measuring actual quality and latency, not just availability. Until verified, editorial review remains necessary. This does not resolve real media generation or missing Threads search/insight permissions.
