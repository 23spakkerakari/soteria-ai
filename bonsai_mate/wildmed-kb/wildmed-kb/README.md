# wildmed-kb: normalized wilderness / emergency scenario corpus

56 sourced emergency scenarios turned into validated, structured records plus a ready-to-query
offline index (SQLite + FTS5) for a small on-device model's retrieval layer.

These records are for **retrieval and evaluation**. They are **not treatment protocols**.
Everything under `source_management` is what the educational source said, paraphrased, and every
record carries `eval.clinical_review = "pending"`.

## Layout

```
schema/scenario.py            Pydantic v2 model (the contract) + controlled vocabularies
data/source_registry.json     per-source org, license, reuse status, access notes
data/raw_records/*.json       hand-normalized records (source shorthand: {"ref": "nols", ...})
data/inbox/                   drop manually downloaded case files here (docx/pdf) for future extraction
data/build/                   generated, do not edit
  scenarios.jsonl             1 fully expanded record per line
  chunks.jsonl                382 typed, embedding-ready chunks with metadata
  scenarios.sqlite            tables below + FTS5 index
  scenario.schema.json        JSON Schema export (for TS/Kotlin/Swift codegen)
  report.json                 counts + validation errors
scripts/build.py              validate -> expand -> chunk -> write all artifacts
scripts/retrieve.py           BM25 + metadata filter retriever, rrf() hook for vectors
```

Build and query:

```bash
pip install pydantic   # only dependency
python3 scripts/build.py
python3 scripts/retrieve.py "my friend is shivering and clumsy in the rain" -k 3
python3 scripts/retrieve.py "chest pain can't breathe" --acuity life_threat --types presentation
```

## Record shape (abridged)

```
id, title, schema_version
source        organization, source_type, title, url, published, authors, license, reuse_status, retrieved
classification category (injury|environmental|medical|mixed), conditions[] (controlled tags),
              primary_condition, mechanism, acuity (life_threat|urgent|semi_urgent|field_manageable), acuity_basis
context       setting, activity, region, terrain, weather, ambient_temp_c, altitude_m, connectivity,
              distance_to_care, responder_training, group, resources_available[]
patient       age_years, age_group, sex, pertinent_history[], medications[], allergies[]
presentation  summary, chief_complaint, symptoms[], exam_findings[], pertinent_negatives[], last_intake_output
vitals[]      time, loc, hr, rr, skin, bp_systolic/diastolic or bp_note, pupils, temp_c, spo2, pain_0_10, note
source_management  assessment[], plan[], anticipated_problems[], teaching_points[], decision_points[{question, source_decision, basis[]}]
outcome       summary, evacuation_mode, final_diagnosis
eval          must_recognize[], must_ask[], must_escalate, must_not_recommend[], derived_from, clinical_review
retrieval     keywords[], lay_queries[], red_flags[]
provenance    extraction_method, extracted_on, verbatim_text_stored=false, notes
```

Units are normalized to Celsius / metres. Vitals are kept as a time series so the model can reason
about trends (e.g. shock vs acute stress response), which the sources treat as a core skill.

## Chunk types (one record -> up to 7 chunks)

| chunk_type | use it when the assistant is... |
|---|---|
| presentation | matching what the user is describing (includes lay phrasings) |
| context | conditioning on environment, distance, resources |
| assessment_plan | looking up what the source did (tagged UNVALIDATED) |
| decision_points | answering "should we evacuate / clear the spine / thaw?" |
| vitals_trend | interpreting serial vitals |
| teaching | pulling general principles and thresholds |
| outcome | grounding expectations about evacuation and final diagnosis |

Each chunk carries `setting, primary_condition, acuity, age_group, reuse_status, clinical_review` so
you can pre-filter before ranking. `chunks.embedding` is an empty BLOB column ready for vectors.

## SQLite tables

`scenarios` (one row + full JSON), `scenario_conditions` (tag index), `vitals`, `red_flags`,
`chunks`, `chunks_fts` (FTS5, porter stemming), `sources`.

## Source coverage

| Source | License / reuse | Records | Status |
|---|---|---|---|
| NOLS Wilderness Medicine, curriculum case studies (English) | All rights reserved | 30 | all 30 ingested with answer keys |
| NOLS blog case studies (newer) | All rights reserved | 19 | new cases ingested; reposts of older cases skipped |
| NOLS Spanish PDFs | All rights reserved | 3 | snakebite, ice-breakthrough hypothermia, altitude/fatigue; translated |
| MedEdPORTAL via PMC (PMC7331961) | CC BY-NC 4.0 | 1 | article body only; appendices not parsed |
| EM Sim Cases | CC BY-SA 4.0 | 3 | stem-only (case detail is in .docx that could not be fetched) |
| JETem | CC BY (verify) | 0 | not ingested yet |
| UCSF / WHO Basic Emergency Care | unverified | 0 | site refused automated fetch; download PDFs to data/inbox/ |
| Red Cross candidate resources | All rights reserved | 0 | behind course portal; better used as clinical-authority layer |
| On Scene CPD | Proprietary | 0 | interactive app behind login; design reference only |

Skipped as reposts of cases already ingested: Cardiac Arrest at the Crag (= CS36), Suspicious
Stomach Pain (= CS19). Titles that look like reposts and were not fetched: Abdominal Pain on the Trail,
Runner Begins Feeling Ill, An Undefined Rash, Spinal Injuries from a Fall, Ankle Injuries while
Climbing, A Freezing Snowmobile Ride, Heat Stress Hits an Entire Group, Bucked off a Horse, '80s
Mystery, A Hot Day Becomes a First Aid Situation, Scenario Near Yellowstone, Falling Through the Ice,
Heat Cramps on the Rio Grande, What to Do About Snakebites, Flu-Like Illness (2023), Hyperglycemia in
the Mountains, Fall from a Horse (2024). Not yet fetched and possibly new: A Slip on a Slope, Slip
Sliding Away, An Injured Ankle on the Hunt, Nauseous in the Heat, Cold Injuries on a Hike, Treating
Burns (2015), and Spanish PDFs 01, 05, 12, 13, 16, 17, 18, 21, 26, 29.

## Things to know before shipping

1. **Licensing.** 52 of 56 records come from all-rights-reserved NOLS material. Records store
   paraphrased facts only (`verbatim_text_stored: false`), but treat them as internal R&D until you
   have written permission. Filter on `reuse_status` before bundling into an app.
   EM Sim Cases is CC BY-SA, so a redistributed derivative must stay CC BY-SA.
2. **Editor-derived fields.** `acuity`, `eval.*`, `retrieval.lay_queries` and `retrieval.red_flags`
   were written during normalization from the source's own answer key. They are a starting point for
   clinician review, not ground truth. `eval.derived_from` says which records rest on learning
   objectives or editor judgment rather than an answer key.
3. **Retrieval eval leakage.** `lay_queries` are indexed, so testing retrieval with those same strings
   is circular. Use held-out phrasings (see sample runs in the build notes).
4. **Coverage gaps.** Adults dominate (48), only 5 adolescents and 3 older adults, no children,
   no aircraft or road-travel settings, no drowning case with full detail, no pregnancy. Heat stroke
   exists only as ED stem records.
5. **Stem-only records** (EM Sim Cases x3) have no vitals; full extraction needs the .docx files.

## Suggested next steps

- Clinician review pass: flip `clinical_review` per record, edit `eval.*`.
- Add embeddings (e.g. a small local sentence model) into `chunks.embedding`, fuse with `rrf()`.
- Ingest the remaining NOLS titles and the JETem wilderness cases; drop UCSF/EM Sim docx files in `data/inbox/`.
- Generate synthetic variants per record (alone vs with companion, no kit, worsening mid-conversation),
  stored with `source_type` = synthetic and a pointer to the parent record.
