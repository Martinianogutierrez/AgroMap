# SDD ledger — plan: 2026-10-29-data-layer-file-step (chat plan)
Pre-flight: PLOTS.files → layersView + upload IIFE; shared AgroPlots API
Task 1: complete — PLOTS.files + AgroPlots.add/addFiles/currentFiles
Task 2: complete — upStepFiles DOM/CSS/i18n
Task 3: complete — step lot|files, AgroOpenDataFiles, DnD .txt titles
Task 4: complete — layersView files card + addFiles act
Task 5: complete — static verify symbols present
Ruling: skip with 0 files OK — plan Review Focus
Ruling: per-lote files on PLOTS[i] — user approach 1
Ruling: no automated browser tests in repo; symbol + manual flow checklist
Final review: cavecrew-reviewer
Final: fixed double-click empty plot — filesCommitLock + !pendingLot guard
Final: fixed scroll jump from capas — skip scrollTo when filesOnly fromMain
Final: fixed dedupe vs current plot files in addFileNames
Final: fixed isTxt require .txt suffix only
Final: minor layout files-only single col + noFocus on close
