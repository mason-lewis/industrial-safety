# Demonstration and evaluation candidates

Video attribution: Önal and Dandıl (2024), **Video Dataset for Safe and Unsafe Behaviours**, version 1, [Mendeley Data](https://data.mendeley.com/datasets/xjmtb22pff/1), DOI **10.17632/xjmtb22pff.1**, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The 24 neutral filenames are full-clip derivatives of original **test** files: resized to 1280 pixels wide, encoded as H.264 CRF 25, with audio removed. Original names, original hashes, original behavior labels and transformations are in `provenance.json`. Output hashes and original split are in `manifest.json`. Keep these files together when redistributing. Eight clips (one per original behavior class) are copied into a new app workspace by the launcher. The originals in the developer workspace are preserved.

These are **candidates for independent annotation**, not 24 independently labeled safety examples. The requested eight positive, eight negative and eight ambiguous examples remain an open task. The original behavior classes do not establish visible hazards in the four application categories. Do not rename a source label into ground truth. Replace or supplement candidates if category coverage is missing. Source recording/session groups are unresolved; keep the candidates together in a held-out group until provenance permits a stronger split. Do not tune on this group and then call it an independent test.

Source footage comes from Eskişehir, Türkiye. It does not establish US jurisdiction, an event date, an exit designation, site PPE requirements or regulatory applicability. Do not identify depicted workers or infer employment records from the clips.

`../submission/pilot/label-and-time.html` is a local form for recording human labels and trial times. `provenance.json` contains original behavior names and should be hidden from blinded labelers. The app sees neutral filenames and pixels only.

## Guidance bundle

`safety-sources.ndjson` contains 114 bounded excerpts from the existing local official federal source snapshot: 29 CFR 1910.5, .12, .22, .36, .37, .132 and .176. Records retain their exact source URL, section, source version, retrieval timestamp, context-completeness marker and stable ID. The source version includes the original snapshot hash. The launcher imports them immutably into the local SQLite/FTS source library. US federal government source text is supplied for reference; inclusion does not establish applicability. No regulatory determination is valid without the relevant exact clause, exceptions, scope and site facts. Missing source text remains unavailable. The complete developer library is not included in the small submission package.

## Fixture boundary

Offline mode uses fixed FunctionModel responses over real decoded evidence. It tests coverage, storage, routing, export and human decisions. It does **not** recognize hazards. Fictional concerns and insufficient-evidence assessments are prominently labeled. Synthetic counters are excluded from reported provider token usage. No paid calls are made.
