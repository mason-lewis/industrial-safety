# Course implementation research record

Research date: 2026-10-23. Scope: public source inspection and local structural analysis. No third-party agent framework was imported. Application code remains React/FastAPI/SQLite with PydanticAI and the pinned model. Licensing below concerns the inspected revision; consult the repository license before copying code. The implementation adopts design patterns, not wholesale source modules.

| Source and inspected revision | Inspected components | Finding and applied decision | License / constraint |
|---|---|---|---|
| [REA](https://github.com/morluto/rea), main `96ea32b7e4e28d3c9cd79a1de4b8e1ba3b4244e9`; executed npm 6.7.0 | Evidence types, evidence completion ledger, frontend parser and relationships | Separate raw observations, derived claims, confidence, provenance and completion. Implemented durable window outcomes, source hashes, gaps and separate coverage/assessment/human status. | MIT. Development tool only; absent from application dependencies. npm package and branch revision are separate provenance. |
| [Frigate](https://github.com/blakeblackshear/frigate), dev `f7d532a09d275cf960fe130af2c7f645c3da5369` | `frigate/review/maintainer.py`, episode creation/update/end | A sustained condition should have one episode and retained occurrences. Implemented conservative adjacent-window grouping with category, camera, source, item and box-IoU gates. | MIT. No Frigate runtime or person-identity tracking. |
| [NVIDIA VSS](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization), develop `d9a8b7c02f13141e528cf99ecebb6acf4c24aa7c` | `chunk_info.py`, stream handler, dedup state | Ordered chunks need durable progress, failure state, recovery and metrics. Implemented sequential manifests, immediate checkpoints and resumable original-budget jobs. | Apache-2.0. No GPU service infrastructure copied. |
| [LensWalk](https://github.com/likanchuan09171/LensWalk), release `c3cdf13a6ffc6b1ce21af3a0c7813178d17fac77` | Scan observer interval splitting and dispatcher | Broad coverage must precede adaptive detail. New Review uses every 10-second interval; legacy adaptive analysis remains secondary. | Apache-2.0. No framework adoption. |
| [MMCTAgent](https://github.com/microsoft/MMCTAgent), main `4b25b1f820111f9efadd4fbb7c1c917b48d9fa7d` | `image_pipeline/core/tools/critic.py` | Critique must inspect original visual evidence. Retained pixels enter independent challenge within bounded packets. | MIT. No claim of identical model quality. |

REA 6.7.0 parsed **71 relevant frontend files with zero parse failures**. Its relationship graph was partial. Approximately 950 reported structural findings are parser/relationship observations, **not 950 verified defects**. The raw local research output was about 56 MB and is excluded from the small submission. It was used to inspect module seams and duplication. No runtime behavior or accuracy claim follows from the count.

## Research papers and evaluation references

- [VideoAgent, arXiv:2403.11481](https://arxiv.org/abs/2403.11481): structured temporal memory and selective retrieval. Decision: extend existing SQLite/FTS memory; no additional vector database.
- [VideoAgent, arXiv:2403.10517](https://arxiv.org/abs/2403.10517): iterative video understanding and selective queries. Decision: preserve adaptive analysis as an optional secondary workflow, never describe sparse query success as full coverage.
- [StreamingBench, arXiv:2411.03628](https://arxiv.org/abs/2411.03628): streaming questions must be evaluated when evidence becomes available. Decision: test source-time causality, playback pacing, future rejection and processing backlog explicitly.
- [SimpleStream, arXiv:2604.02317](https://arxiv.org/abs/2604.02317): recent-frame streaming baseline motivates a simpler screening comparator. This is an evaluation idea, not evidence of our performance.
- [Video-MME, arXiv:2405.21075](https://arxiv.org/abs/2405.21075): duration-sensitive evaluation. Decision: verify short tails and ten-minute inputs separately.
- [FiftyOne detection evaluation](https://docs.voxel51.com/user_guide/evaluation/detections.html): examine misses and false alarms, not just aggregate scores. Decision: deterministic one-to-one temporal matching at IoU ≥0.5 plus a failure gallery with evidence. Temporal matching here is explicitly implemented in the existing evaluator; it is not a copied FiftyOne metric.

## Alternatives and supported novelty

Official product pages: [Intenseye](https://www.intenseye.com/), [Protex AI](https://www.protex.ai/), [viAct AI modules](https://www.viact.ai/aimodules). Their published offerings already include workplace-video analytics; Protex also describes agentic workflows. These are vendor capability claims, not independent accuracy comparisons. We did not run competitor products or infer their private architecture, pricing, coverage guarantees or deployment costs.

The defensible hypothesis is a useful combination of inexpensive local operation, explicit sampled-interval coverage, inspectable retained evidence, source-grounded challenge and human disposition. The prototype is not the first AI safety platform, and its user benefit remains to be measured in a small pilot. Local operation refers to storage/orchestration; hosted analysis transmits selected pixels to the selected model provider.

## Dataset and source applicability

[Önal and Dandıl, 2024](https://data.mendeley.com/datasets/xjmtb22pff/1), DOI 10.17632/xjmtb22pff.1, CC BY 4.0: 691 released clips, original split 566 train / 125 test. Clips are short; the source-camera collection spans a much larger recording period. Do not confuse collection hours with released continuous observation coverage. Türkiye footage cannot establish US rule applicability. The packaged 24 test candidates preserve provenance and need independent category labels and source-group review.

## Cost sources and interpretation

[Model pricing](https://developers.openai.com/api/docs/models/gpt-6-luna) and [prompt cache accounting](https://developers.openai.com/api/docs/guides/prompt-caching), checked 2026-10-23. Versioned configured rates per million tokens: uncached input $0.10; cached input $0.01; reported 30-minute cache writes $0.125; 12-hour writes $0.25; output $0.50. Model is pinned to `gpt-6-luna`; no fallback. An unresolved service tier or cache-write split makes cost unknown. Image allowances reserve headroom; actual reported image tokens are already part of input usage. Never add that allowance again to calculated usage.

All engineering validation is offline. Fixture timing, token counters and fixed conclusions are not evidence of hosted latency, provider billing or hazard accuracy. The final report must add user-operated traces and label them with the tested version.
