# GeoAI and deep-learning notebooks

- `pro_geoai_info what=environment` FIRST: Image Analyst licence, torch, CUDA GPU. Without a GPU, inference is
  slow (731 s measured for one 1024x1024 image with text detection on CPU).
- `pro_geoai_infer task=detect|classify_pixels|classify_objects|change|embeddings|similar|foundation` runs a
  trained model (.dlpk/.emd) or Esri foundation models.
- `pro_geoai_train action=export|train|accuracy|train_and_detect` - long; run with `pro_batch(background=true)`.
- `pro_geoai_text` (open-vocabulary detection from a sentence): in two CPU runs it returned zero-size boxes at
  the raster's top-left corner; do NOT present its output as locations without checking.

## Training notebooks - `pro_dl_notebook`
Interview first, never guess: call with only `goal` -> `status=ask_user` (recommended model + questions) or
`status=choose` (plain "classification" is ambiguous: pixel / object / detect). Ask the user, call again with
`training_data`, `batch_size`, `epochs` (+ options) -> `status=created`. Object classification is
FeatureClassifier; change detection has two routes (Esri's ready-made Building Change Detection, no training,
10-20 cm imagery for both dates; or a trainable ChangeDetector). The notebook lands in the open Pro project's
folder; add it in Pro via Catalog > Notebooks > Add and Open Notebook. `import arcgis.learn` takes ~2 minutes cold.
