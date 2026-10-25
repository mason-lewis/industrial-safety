# Overhaul verification

Verified on October 5, 2026, in this local workspace. No external deployment, paid provider request or cloud training was used.

- Backend: **156 tests passed**. This includes the original 129 tests and 27 new contract/failure cases.
- Frontend: **18 tests passed** across five test files.
- Frontend: TypeScript check and Vite production build passed.
- Generated schema contracts matched both TypeScript files.
- Python sources compiled. The locked dependency sync completed without replacement packages.
- The standalone isolated fixture command completed replay, CV candidate, observer, safety reviewer, challenger, finding and human decision with **zero provider requests**.
- The existing cached scene comparison ran with fake providers. Six mocked requests completed. Its results are in the existing comparison cache folder.
- The synthetic event evaluation exported `data/evals/reports/plumbing.json`. Its scores measure fixture plumbing; they are not real detector or safety accuracy.
- FastAPI OpenAPI includes all required route groups.
- Browser checks reached Monitoring, Safety Review, Labeling, Audit and Costs & Coverage. Retained historical evidence loaded. Audit filtering produced the expected empty result. Both labeling areas rendered. Budget status and UTC windows rendered.
- Local development servers started for verification were shut down afterward.

The backend emits an existing TorchScript deprecation warning. This host also emits an existing PyAV/OpenCV duplicate AVFoundation-class warning. Neither warning caused a failed check.

Real provider usage, live RTSP recovery, calibration and actual local model training need external inputs. No passing result is claimed for those operations.

![Costs and coverage screen](overhaul-costs.jpg)

## Trial reset follow-up

The full backend suite now passes **162 tests**, including six new reset tests. Frontend checks still pass **18 tests**, generated types, TypeScript and production build.
Reset and restore were exercised only in isolated test roots. Existing user data was not reset. Tests preserve draft labels, frozen dataset frame references, weights, model evaluations, original media bytes and outstanding budget reservations. They verify stale previews, running-job blocks, replay-key reuse, inactive old caches and restart/migration behavior.
Two successive `scripts/safety fixture-replay` invocations completed in different automatically created temporary directories with zero provider requests.
