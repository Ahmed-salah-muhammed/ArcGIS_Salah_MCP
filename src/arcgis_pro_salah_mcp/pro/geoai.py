"""GeoAI: deep learning, vision-language models and foundation-model embeddings.

Three generations of imagery AI live side by side in ArcGIS, and this module
exposes all three because they solve different problems:

**1. Classic supervised deep learning** (``arcpy.ia``)
    You bring a trained ``.dlpk`` and run it: ``detect_objects`` (bounding boxes
    / masks), ``classify_pixels`` (semantic segmentation), ``classify_objects``
    (label existing features). Accurate, but only for the classes it was trained
    on, and someone has to train it first — which is why
    ``export_training_data`` + ``train_model`` are here too.

**2. Vision-language / open-vocabulary detection** (``DetectObjectsUsingText``)
    No training, no ``.dlpk``, no fixed class list: you describe what you want in
    plain words ("solar panel", "blue tarp roof", "flooded road") and a
    grounded VLM finds it. This is the tool that makes satellite imagery
    queryable by *language*, and it is usually the right first thing to try.

**3. Foundation-model embeddings** (``arcpy.geoai``)
    A vision foundation model (DINOv3-class, SAM, and the other backbones
    shipped in the ArcGIS pretrained-model catalog) turns imagery or features
    into embedding vectors. Once embedded, similarity search replaces training
    entirely: label ONE example rooftop and
    ``find_similar_features`` retrieves the rest across the whole scene. This is
    the "image-context based" workflow — the model never learned your class, it
    just knows what looks alike.

Everything here delegates to ArcGIS; nothing re-implements a model. The value
this module adds is (a) sane defaults, (b) validating arguments locally so a
mistake costs milliseconds instead of a 20-minute GPU run, and (c) the
``workflow_*`` helpers that chain the multi-step pipelines into one call.

Licensing: the deep-learning tools need the **Image Analyst** extension and the
``deep-learning-essentials`` conda packages. :func:`check_environment` reports
exactly what is missing before you burn time on a run.
"""
from __future__ import annotations

import os
from typing import Any

from .._result import err, guard, ok
from . import cache, ops

# TrainDeepLearningModel's model_type vocabulary, grouped by task so an agent can
# pick one without guessing. Taken from the installed arcpy signature.
MODEL_TYPES: dict[str, list[str]] = {
    "object_detection": [
        "FASTERRCNN", "RETINANET", "SSD", "YOLOV3", "MMDETECTION", "DETREG", "RTDETRV2",
    ],
    "pixel_classification": [
        "UNET", "PSPNET", "DEEPLAB", "MMSEGMENTATION", "MULTITASK_ROADEXTRACTOR",
        "CONNECTNET", "MAXDEEPLAB",
    ],
    "instance_segmentation": ["MASKRCNN", "SAMLORA"],
    "object_classification": ["FEATURE_CLASSIFIER"],
    "change_detection": ["CHANGEDETECTOR", "SIAMMASK"],
    "edge_detection": ["BDCN_EDGEDETECTOR", "HED_EDGEDETECTOR"],
    "image_translation": ["CYCLEGAN", "PIX2PIX", "PIX2PIXHD", "SUPERRESOLUTION"],
    "captioning": ["IMAGECAPTIONER"],
    "tracking": ["DEEPSORT"],
    "timeseries": ["PSETAE", "CLIMAX"],
    "point_cloud": ["3DRCNET"],
}

# metadata_format for ExportTrainingDataForDeepLearning, keyed by what you intend
# to train. Getting this pairing wrong is the single most common reason a
# training run fails hours later, so it is validated up front.
TRAINING_FORMATS: dict[str, list[str]] = {
    "object_detection": ["KITTI_rectangles", "PASCAL_VOC_rectangles"],
    "pixel_classification": ["Classified_Tiles"],
    "instance_segmentation": ["RCNN_Masks"],
    "object_classification": ["Labeled_Tiles", "MultiLabeled_Tiles", "Imagenet"],
    "panoptic": ["Panoptic_Segmentation"],
    "image_translation": ["Export_Tiles", "CycleGAN"],
}

_ALL_MODEL_TYPES = {m for group in MODEL_TYPES.values() for m in group}
_ALL_FORMATS = {f for group in TRAINING_FORMATS.values() for f in group}


def _ia():
    """Image Analyst module — raises a clear error when the extension is absent."""
    arcpy = ops._arcpy()
    try:
        import arcpy.ia as ia  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "arcpy.ia (Image Analyst) is unavailable. The GeoAI tools need the "
            "Image Analyst extension."
        ) from exc
    if arcpy.CheckExtension("ImageAnalyst") != "Available":
        raise RuntimeError(
            "The Image Analyst extension is not licensed/available on this machine."
        )
    arcpy.CheckOutExtension("ImageAnalyst")
    return ia


def _geoai():
    """arcpy.geoai — the foundation-model / embeddings module (Pro 3.3+)."""
    try:
        import arcpy.geoai as g  # type: ignore

        return g
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "arcpy.geoai is unavailable — it needs ArcGIS Pro 3.3+ with the "
            "deep-learning libraries installed."
        ) from exc


# --- Environment -----------------------------------------------------------

@guard
def check_environment() -> dict:
    """What is actually available here: extensions, GPU, torch, model catalog.

    Call this BEFORE a long run. Deep-learning failures in ArcGIS are notoriously
    late and opaque; this surfaces the usual causes up front.
    """
    arcpy = ops._arcpy()
    report: dict[str, Any] = {}

    report["image_analyst"] = arcpy.CheckExtension("ImageAnalyst")
    report["spatial_analyst"] = arcpy.CheckExtension("Spatial")

    for label, mod in (("arcpy.ia", "arcpy.ia"), ("arcpy.geoai", "arcpy.geoai")):
        try:
            __import__(mod)
            report[label] = "available"
        except Exception as exc:  # noqa: BLE001
            report[label] = f"unavailable: {exc}"

    try:
        import torch  # type: ignore

        report["torch"] = {
            "version": torch.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
            "device_name": (
                torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
            ),
        }
    except Exception as exc:  # noqa: BLE001
        report["torch"] = f"unavailable: {exc} (install deep-learning-essentials)"

    try:
        import arcgis  # type: ignore

        report["arcgis_api"] = getattr(arcgis, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        report["arcgis_api"] = "not installed"

    gpu_ready = isinstance(report.get("torch"), dict) and report["torch"]["cuda_available"]
    report["ready_for_gpu_inference"] = gpu_ready
    report["notes"] = (
        "GPU ready." if gpu_ready
        else "No CUDA GPU detected — inference will run on CPU and be much slower. "
             "Set processor_type='CPU' explicitly to avoid a confusing failure."
    )
    return ok(report)


@guard
def describe_model(model_definition: str) -> dict:
    """Read a ``.dlpk`` / ``.emd`` so the agent knows what the model expects.

    Tells you the classes it predicts, the chip size it wants and the framework
    — all the things you otherwise discover by failing.
    """
    import json

    path = str(model_definition)
    emd_path = path
    if path.lower().endswith(".dlpk"):
        # A .dlpk is a zip; the .emd inside carries the metadata.
        import zipfile

        try:
            with zipfile.ZipFile(path) as z:
                emd_name = next(
                    (n for n in z.namelist() if n.lower().endswith(".emd")), None
                )
                if emd_name is None:
                    return err("No .emd found inside the .dlpk.", code="no_emd")
                meta = json.loads(z.read(emd_name).decode("utf-8"))
        except zipfile.BadZipFile:
            return err("The .dlpk is not a readable zip archive.", code="bad_dlpk")
    else:
        if not os.path.exists(emd_path):
            return err(f"Model definition not found: {emd_path}", code="not_found")
        with open(emd_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)

    classes = meta.get("Classes") or []
    return ok(
        {
            "path": path,
            "framework": meta.get("Framework"),
            "model_type": meta.get("ModelType"),
            "model_configuration": meta.get("ModelConfiguration"),
            "description": meta.get("Description"),
            "image_width": meta.get("ImageWidth"),
            "image_height": meta.get("ImageHeight"),
            "extent_type": meta.get("ExtractBands"),
            "min_cell_size": meta.get("MinCellSize"),
            "max_cell_size": meta.get("MaxCellSize"),
            "classes": [
                {"value": c.get("Value"), "name": c.get("Name")} for c in classes
            ],
            "class_count": len(classes),
            "inference_function": meta.get("InferenceFunction"),
        }
    )


@guard
def list_model_types(task: str | None = None) -> dict:
    """Valid ``model_type`` values for training, grouped by task. Pure lookup."""
    if task:
        key = task.strip().lower()
        if key not in MODEL_TYPES:
            return err(f"Unknown task '{task}'.", valid=sorted(MODEL_TYPES))
        return ok(
            {
                "task": key,
                "model_types": MODEL_TYPES[key],
                "training_formats": TRAINING_FORMATS.get(key, []),
            }
        )
    return ok({"by_task": MODEL_TYPES, "training_formats": TRAINING_FORMATS})


# --- 2. Vision-language (open-vocabulary) detection -------------------------

@guard
def detect_objects_by_text(
    in_raster: str,
    class_name: str,
    out_features: str | None = None,
    box_threshold: float = 0.3,
    text_threshold: float = 0.3,
    tile_size: int = 512,
    overlap: int = 0,
    use_gpu: bool | None = None,
    extent: str | None = None,
    cell_size: float | None = None,
    output_json: str | None = None,
) -> dict:
    """Find objects described in PLAIN TEXT — no model, no training, no class list.

    ``class_name`` is a natural-language prompt ("solar panel", "swimming pool",
    "damaged roof"). A grounded vision-language model (GroundingDINO + a BERT
    text encoder) locates matching objects in the imagery.

    Thresholds: ``box_threshold`` is how confident the box must be,
    ``text_threshold`` how strongly it must match the words. Both default to
    0.3; raise them when you get false positives, lower them when you get
    nothing.

    Two behaviours here exist because of measured quirks in the ArcGIS tool:

    * **The extent is always supplied.** Called without one, the tool fails
      inside its own code with "cannot access local variable 'extent'", so when
      you do not pass an extent this reads the raster's own and sends it as the
      JSON the tool expects (a space-separated extent string makes it fail later
      with a JSON parse error instead).
    * **``use_gpu`` is omitted unless you set it.** The parameter rejects the
      obvious "GPU"/"CPU" strings ("ERROR 000628: Cannot set input into
      parameter useGPU").

    **This is slow without a GPU.** Measured on a CPU-only machine: 731 s for a
    single 1024x1024 image at tile_size 512. Check
    :func:`check_environment` first, and consider ``pro_job_submit`` so the
    agent stays responsive while it runs.
    """
    if not 0 < box_threshold <= 1 or not 0 < text_threshold <= 1:
        return err("box_threshold and text_threshold must be between 0 and 1.")

    ia = _ia()
    arcpy = ops._arcpy()

    if extent is None:
        # Esri's tool crashes on a missing extent, and only accepts it as JSON.
        try:
            import json as _json

            raster = arcpy.Raster(in_raster)
            box = raster.extent
            wkid = getattr(getattr(box, "spatialReference", None), "factoryCode", None)
            payload = {
                "xmin": box.XMin, "ymin": box.YMin,
                "xmax": box.XMax, "ymax": box.YMax,
            }
            if wkid:
                payload["spatialReference"] = {"wkid": wkid}
            extent = _json.dumps(payload)
        except Exception as exc:  # noqa: BLE001
            return err(
                f"Could not read the extent of {in_raster}, which this tool "
                f"requires: {exc}",
                code="no_extent",
            )

    kwargs = {
        "input_raster_dataset": in_raster,
        "class_name": class_name,
        "box_threshold": box_threshold,
        "text_threshold": text_threshold,
        "tile_size": tile_size,
        "overlap": overlap,
        "extent": extent,
    }
    if use_gpu is not None:
        kwargs["useGPU"] = use_gpu
    if cell_size is not None:
        kwargs["cell_size"] = cell_size
    if output_json:
        kwargs["output_json"] = output_json

    result = ia.DetectObjectsUsingText(**kwargs)

    payload: dict[str, Any] = {
        "prompt": class_name,
        "raster": in_raster,
        "box_threshold": box_threshold,
        "text_threshold": text_threshold,
        "engine": "vision-language (open vocabulary, GroundingDINO)",
    }

    # The tool returns its detections as a JSON string rather than a dataset,
    # so surface the scores and labels directly — that is the answer the agent
    # asked for, and re-parsing it downstream would be pure ceremony.
    raw = str(result) if result is not None else ""
    detections = None
    if raw.strip().startswith("{"):
        try:
            import json as _json

            parsed = _json.loads(raw)
            names = parsed.get("idToClassName", {})
            scores = parsed.get("scores", []) or []
            ids = parsed.get("classNameIds", []) or []
            detections = [
                {"label": names.get(str(cid), str(cid)), "score": round(float(sc), 4)}
                for cid, sc in zip(ids, scores)
            ]
            payload["detections"] = detections
            payload["detection_count"] = len(detections)
            payload["bounding_boxes"] = parsed.get("boundingBoxes")
        except Exception:  # noqa: BLE001
            payload["output"] = raw
    else:
        payload["output"] = raw
        payload.update(_count_output(raw))

    if out_features and detections is None:
        payload["note"] = (
            "The tool returned JSON rather than a dataset; out_features was not used."
        )
    if output_json:
        payload["json"] = output_json
    return ok(payload)


def _count_output(dataset: str | None) -> dict:
    """Feature count for a produced dataset — best effort, never fatal."""
    if not dataset:
        return {}
    try:
        arcpy = ops._arcpy()
        if arcpy.Exists(dataset):
            cache.invalidate(dataset)
            return {"feature_count": int(arcpy.management.GetCount(dataset)[0])}
    except Exception:  # noqa: BLE001
        pass
    return {}


# --- 1. Supervised deep learning -------------------------------------------

@guard
def detect_objects(
    in_raster: str,
    out_features: str,
    model_definition: str,
    arguments: dict | None = None,
    run_nms: bool = True,
    confidence_field: str = "Confidence",
    class_field: str = "Class",
    max_overlap_ratio: float = 0.0,
    processing_mode: str = "PROCESS_AS_MOSAICKED_IMAGE",
    processor_type: str | None = None,
    batch_size: int | None = None,
) -> dict:
    """Run a trained detection model (``.dlpk``/``.emd``) over imagery.

    Non-maximum suppression is ON by default: tiled inference duplicates objects
    that straddle a tile boundary, and without NMS every such object is counted
    twice.
    """
    arcpy = ops._arcpy()
    ia = _ia()

    args = dict(arguments or {})
    if batch_size is not None:
        args.setdefault("batch_size", batch_size)
    arg_string = _format_arguments(args)

    with _processor(arcpy, processor_type):
        result = ia.DetectObjectsUsingDeepLearning(
            in_raster=in_raster,
            out_detected_objects=out_features,
            in_model_definition=model_definition,
            arguments=arg_string,
            run_nms="NMS" if run_nms else "NO_NMS",
            confidence_score_field=confidence_field,
            class_value_field=class_field,
            max_overlap_ratio=max_overlap_ratio,
            processing_mode=processing_mode,
        )

    payload = {
        "raster": in_raster,
        "output": out_features,
        "model": model_definition,
        "nms": run_nms,
        "arguments": args,
        "messages": _messages(result),
    }
    payload.update(_count_output(out_features))
    return ok(payload)


@guard
def classify_pixels(
    in_raster: str,
    model_definition: str,
    out_raster: str | None = None,
    out_features: str | None = None,
    arguments: dict | None = None,
    processing_mode: str = "PROCESS_AS_MOSAICKED_IMAGE",
    processor_type: str | None = None,
) -> dict:
    """Semantic segmentation — classify every pixel (land cover, roads, damage)."""
    arcpy = ops._arcpy()
    ia = _ia()
    if not out_raster and not out_features:
        return err("Provide out_raster and/or out_features.")

    with _processor(arcpy, processor_type):
        raster = ia.ClassifyPixelsUsingDeepLearning(
            in_raster=in_raster,
            in_model_definition=model_definition,
            arguments=_format_arguments(arguments or {}),
            processing_mode=processing_mode,
            out_classified_folder=None,
            out_featureclass=out_features,
        )
        if out_raster is not None and raster is not None:
            raster.save(out_raster)

    payload = {
        "raster": in_raster,
        "model": model_definition,
        "out_raster": out_raster,
        "out_features": out_features,
    }
    payload.update(_count_output(out_features))
    return ok(payload)


@guard
def classify_objects(
    in_raster: str,
    out_features: str,
    model_definition: str,
    in_features: str | None = None,
    class_label_field: str | None = None,
    arguments: dict | None = None,
    processing_mode: str = "PROCESS_AS_MOSAICKED_IMAGE",
    processor_type: str | None = None,
) -> dict:
    """Label EXISTING features from imagery (e.g. classify each building's roof)."""
    arcpy = ops._arcpy()
    ia = _ia()
    with _processor(arcpy, processor_type):
        result = ia.ClassifyObjectsUsingDeepLearning(
            in_raster=in_raster,
            out_feature_class=out_features,
            in_model_definition=model_definition,
            in_features=in_features,
            class_label_field=class_label_field,
            processing_mode=processing_mode,
            model_arguments=_format_arguments(arguments or {}),
        )
    payload = {
        "raster": in_raster,
        "in_features": in_features,
        "output": out_features,
        "model": model_definition,
        "messages": _messages(result),
    }
    payload.update(_count_output(out_features))
    return ok(payload)


@guard
def detect_change(
    from_raster: str,
    to_raster: str,
    model_definition: str,
    out_raster: str,
    arguments: dict | None = None,
    processor_type: str | None = None,
) -> dict:
    """Bi-temporal change detection between two co-registered rasters."""
    arcpy = ops._arcpy()
    ia = _ia()
    with _processor(arcpy, processor_type):
        raster = ia.DetectChangeUsingDeepLearning(
            from_raster=from_raster,
            to_raster=to_raster,
            in_model_definition=model_definition,
            arguments=_format_arguments(arguments or {}),
        )
        if raster is not None:
            raster.save(out_raster)
    return ok(
        {
            "from": from_raster,
            "to": to_raster,
            "model": model_definition,
            "output": out_raster,
        }
    )


@guard
def non_maximum_suppression(
    in_features: str,
    out_features: str,
    confidence_field: str = "Confidence",
    class_field: str | None = None,
    max_overlap_ratio: float = 0.0,
) -> dict:
    """De-duplicate overlapping detections after tiled inference."""
    ia = _ia()
    ia.NonMaximumSuppression(
        in_featureclass=in_features,
        confidence_score_field=confidence_field,
        out_featureclass=out_features,
        class_value_field=class_field,
        max_overlap_ratio=max_overlap_ratio,
    )
    payload = {"input": in_features, "output": out_features}
    payload.update(_count_output(out_features))
    return ok(payload)


# --- Training --------------------------------------------------------------

@guard
def export_training_data(
    in_raster: str,
    out_folder: str,
    in_class_data: str | None = None,
    task: str = "object_detection",
    metadata_format: str | None = None,
    image_chip_format: str = "TIFF",
    tile_size: int = 256,
    stride: int | None = None,
    class_value_field: str | None = None,
    rotation_angle: int = 0,
    only_tiles_with_features: bool = True,
    reference_system: str = "MAP_SPACE",
) -> dict:
    """Cut imagery + labels into training chips.

    ``metadata_format`` must match what you intend to train — pass ``task`` and
    the correct default is chosen, or name the format explicitly. A mismatch here
    is the classic "trained for two hours then failed" bug, so it is validated
    before anything is written.
    """
    task_key = task.strip().lower()
    if metadata_format is None:
        if task_key not in TRAINING_FORMATS:
            return err(f"Unknown task '{task}'.", valid=sorted(TRAINING_FORMATS))
        metadata_format = TRAINING_FORMATS[task_key][0]
    elif metadata_format not in _ALL_FORMATS:
        return err(
            f"Unknown metadata_format '{metadata_format}'.", valid=sorted(_ALL_FORMATS)
        )
    elif task_key in TRAINING_FORMATS and metadata_format not in TRAINING_FORMATS[task_key]:
        return err(
            f"metadata_format '{metadata_format}' does not match task '{task_key}'. "
            f"Valid for this task: {TRAINING_FORMATS[task_key]}",
            code="format_task_mismatch",
        )

    step = stride if stride is not None else tile_size // 2
    ia = _ia()
    ia.ExportTrainingDataForDeepLearning(
        in_raster=in_raster,
        out_folder=out_folder,
        in_class_data=in_class_data,
        image_chip_format=image_chip_format,
        tile_size_x=tile_size,
        tile_size_y=tile_size,
        stride_x=step,
        stride_y=step,
        output_nofeature_tiles=(
            "ONLY_TILES_WITH_FEATURES" if only_tiles_with_features else "ALL_TILES"
        ),
        metadata_format=metadata_format,
        class_value_field=class_value_field,
        rotation_angle=rotation_angle,
        reference_system=reference_system,
    )

    chips = 0
    images_dir = os.path.join(out_folder, "images")
    try:
        if os.path.isdir(images_dir):
            chips = len(os.listdir(images_dir))
    except OSError:
        pass

    return ok(
        {
            "raster": in_raster,
            "out_folder": out_folder,
            "metadata_format": metadata_format,
            "task": task_key,
            "tile_size": tile_size,
            "stride": step,
            "chips_written": chips,
            "next_step": "pro_geoai_train_model with this out_folder as in_folder",
        }
    )


@guard
def train_model(
    in_folder: str,
    out_folder: str,
    model_type: str,
    max_epochs: int = 20,
    batch_size: int = 4,
    arguments: dict | None = None,
    learning_rate: float | None = None,
    backbone_model: str | None = None,
    pretrained_model: str | None = None,
    validation_percentage: int = 10,
    stop_training: bool = True,
    freeze_model: bool = True,
    processor_type: str | None = None,
) -> dict:
    """Train a model from exported chips. Long-running — consider pro_job_submit."""
    mt = model_type.strip().upper()
    if mt not in _ALL_MODEL_TYPES:
        return err(
            f"Unknown model_type '{model_type}'. Call pro_geoai_model_types.",
            valid=sorted(_ALL_MODEL_TYPES),
        )

    arcpy = ops._arcpy()
    ia = _ia()
    with _processor(arcpy, processor_type):
        result = ia.TrainDeepLearningModel(
            in_folder=in_folder,
            out_folder=out_folder,
            max_epochs=max_epochs,
            model_type=mt,
            batch_size=batch_size,
            arguments=_format_arguments(arguments or {}),
            learning_rate=learning_rate,
            backbone_model=backbone_model,
            pretrained_model=pretrained_model,
            validation_percentage=validation_percentage,
            stop_training="STOP_TRAINING" if stop_training else "CONTINUE_TRAINING",
            freeze="FREEZE_MODEL" if freeze_model else "UNFREEZE_MODEL",
        )

    dlpk = None
    try:
        for name in os.listdir(out_folder):
            if name.lower().endswith(".dlpk"):
                dlpk = os.path.join(out_folder, name)
                break
    except OSError:
        pass

    return ok(
        {
            "in_folder": in_folder,
            "out_folder": out_folder,
            "model_type": mt,
            "epochs": max_epochs,
            "batch_size": batch_size,
            "model_package": dlpk,
            "messages": _messages(result),
        }
    )


@guard
def compute_accuracy(
    detected_features: str,
    ground_truth_features: str,
    out_accuracy_table: str,
    out_report: str | None = None,
    detected_class_field: str | None = None,
    ground_truth_class_field: str | None = None,
    min_iou: float = 0.5,
) -> dict:
    """Score detections against ground truth (mAP / precision / recall)."""
    ia = _ia()
    ia.ComputeAccuracyForObjectDetection(
        detected_features=detected_features,
        ground_truth_features=ground_truth_features,
        out_accuracy_table=out_accuracy_table,
        out_accuracy_report=out_report,
        detected_class_value_field=detected_class_field,
        ground_truth_class_value_field=ground_truth_class_field,
        min_iou=min_iou,
    )

    rows: list[dict] = []
    try:
        arcpy = ops._arcpy()
        fields = [f.name for f in arcpy.ListFields(out_accuracy_table)]
        with arcpy.da.SearchCursor(out_accuracy_table, fields) as cur:
            for row in cur:
                rows.append(dict(zip(fields, [ops._scalar(v) for v in row])))
    except Exception:  # noqa: BLE001
        pass

    return ok(
        {
            "detected": detected_features,
            "ground_truth": ground_truth_features,
            "accuracy_table": out_accuracy_table,
            "report": out_report,
            "min_iou": min_iou,
            "metrics": rows,
        }
    )


# --- 3. Foundation models & embeddings --------------------------------------

@guard
def generate_embeddings(
    in_data: str,
    out_embeddings: str,
    model_definition: str,
    arguments: dict | None = None,
) -> dict:
    """Embed imagery/features with a vision foundation model (DINOv3-class, SAM…).

    The output is a feature class carrying an embedding vector per feature. On
    its own that is not an answer — it is the *index* that
    :func:`find_similar_features` searches. This is the heart of the
    "image-context" workflow: no class list, no training, just "more like this".
    """
    g = _geoai()
    result = g.GenerateEmbeddingsUsingAIModels(
        in_data=in_data,
        out_embeddings_feature_class=out_embeddings,
        in_model_definition_file=model_definition,
        arguments=_format_arguments(arguments or {}),
    )
    payload = {
        "input": in_data,
        "output": out_embeddings,
        "model": model_definition,
        "messages": _messages(result),
        "next_step": "pro_geoai_find_similar with these embeddings + a query feature",
    }
    payload.update(_count_output(out_embeddings))
    return ok(payload)


@guard
def find_similar_features(
    embedding_features: str,
    query_features: str,
    out_features: str,
    threshold: float = 0.8,
) -> dict:
    """Retrieve everything that LOOKS LIKE your example — one label, no training.

    ``query_features`` holds the example(s) you care about; every embedded
    feature above ``threshold`` cosine similarity comes back. Lower the threshold
    to widen the net, raise it to tighten precision.
    """
    if not 0 < threshold <= 1:
        return err("threshold must be between 0 and 1 (cosine similarity).")

    g = _geoai()
    result = g.FindSimilarFeaturesUsingEmbeddings(
        embedding_features=embedding_features,
        query_features=query_features,
        out_embeddings_feature_class=out_features,
        threshold=threshold,
    )
    payload = {
        "embeddings": embedding_features,
        "query": query_features,
        "output": out_features,
        "threshold": threshold,
        "messages": _messages(result),
    }
    payload.update(_count_output(out_features))
    return ok(payload)


@guard
def extract_features_with_foundation_models(
    in_raster: str,
    out_location: str,
    out_prefix: str,
    pretrained_models: list[str] | str | None = None,
    area_of_interest: str | None = None,
    confidence_threshold: float | None = None,
    mode: str = "Infer and Postprocess",
    test_time_augmentation: bool = False,
    save_intermediate: bool = False,
) -> dict:
    """Run Esri's pretrained foundation models (buildings, roads, trees…) end to end.

    This is the batteries-included path: no training, no chip export — point it at
    imagery, name the pretrained models, and it infers *and* post-processes
    (regularises building footprints, connects road centrelines) in one go.
    """
    g = _geoai()
    models = pretrained_models
    if isinstance(models, list):
        models = ";".join(str(m) for m in models)
    result = g.ExtractFeaturesUsingAIModels(
        in_raster=in_raster,
        mode=mode,
        out_location=out_location,
        out_prefix=out_prefix,
        area_of_interest=area_of_interest,
        pretrained_models=models,
        confidence_threshold=confidence_threshold,
        save_intermediate_output="TRUE" if save_intermediate else "FALSE",
        test_time_augmentation="TRUE" if test_time_augmentation else "FALSE",
    )
    return ok(
        {
            "raster": in_raster,
            "out_location": out_location,
            "prefix": out_prefix,
            "models": models,
            "mode": mode,
            "messages": _messages(result),
        }
    )


@guard
def embeddings_to_fields(
    in_features: str,
    out_features: str,
    embedding_field: str = "embedding",
    field_prefix: str = "emb_",
) -> dict:
    """Explode an embedding vector into numeric fields (for clustering/ML)."""
    g = _geoai()
    g.ExtractEmbeddingToFields(
        in_features=in_features,
        out_features=out_features,
        embedding_field=embedding_field,
        output_field_prefix=field_prefix,
    )
    payload = {"input": in_features, "output": out_features, "prefix": field_prefix}
    payload.update(_count_output(out_features))
    return ok(payload)


# --- Chained workflows ------------------------------------------------------

@guard
def workflow_text_to_layer(
    in_raster: str,
    prompt: str,
    out_features: str,
    box_threshold: float = 0.3,
    text_threshold: float = 0.3,
    apply_nms: bool = True,
    max_overlap_ratio: float = 0.3,
) -> dict:
    """Imagery + a sentence -> a finished, de-duplicated feature layer. ONE call.

    Detect by text, then (optionally) run NMS to remove tile-boundary duplicates.
    This is the demo workflow: "find every solar panel in this scene" with no
    model and no training.
    """
    detect = detect_objects_by_text(
        in_raster,
        prompt,
        None,
        box_threshold,
        text_threshold,
    )
    if not detect.get("ok"):
        return detect

    raw = detect["data"].get("output")
    steps = [{"step": "detect_by_text", **detect["data"]}]

    if not raw:
        return err("Text detection produced no output dataset.", steps=steps)

    final = raw
    if apply_nms:
        nms = non_maximum_suppression(
            raw, out_features, "Confidence", "Class", max_overlap_ratio
        )
        steps.append({"step": "nms", **(nms.get("data") or {"error": nms.get("error")})})
        if nms.get("ok"):
            final = out_features
    else:
        arcpy = ops._arcpy()
        arcpy.management.CopyFeatures(raw, out_features)
        final = out_features

    payload = {"prompt": prompt, "raster": in_raster, "output": final, "steps": steps}
    payload.update(_count_output(final))
    return ok(payload)


@guard
def workflow_train_and_detect(
    in_raster: str,
    training_labels: str,
    work_folder: str,
    out_features: str,
    task: str = "object_detection",
    model_type: str = "FASTERRCNN",
    max_epochs: int = 20,
    tile_size: int = 256,
    batch_size: int = 4,
    class_value_field: str | None = None,
) -> dict:
    """Labels -> chips -> trained model -> detections, in ONE call.

    Genuinely long-running (training dominates). Submit it through
    ``pro_job_submit`` if you want the agent to stay responsive while it runs.
    """
    chips_folder = os.path.join(work_folder, "chips")
    model_folder = os.path.join(work_folder, "model")
    steps: list[dict] = []

    exported = export_training_data(
        in_raster,
        chips_folder,
        training_labels,
        task=task,
        tile_size=tile_size,
        class_value_field=class_value_field,
    )
    steps.append({"step": "export_training_data", "ok": exported.get("ok"),
                  **(exported.get("data") or {"error": exported.get("error")})})
    if not exported.get("ok"):
        return {"ok": False, "error": "Training-data export failed.", "data": {"steps": steps}}

    trained = train_model(
        chips_folder, model_folder, model_type,
        max_epochs=max_epochs, batch_size=batch_size,
    )
    steps.append({"step": "train_model", "ok": trained.get("ok"),
                  **(trained.get("data") or {"error": trained.get("error")})})
    if not trained.get("ok"):
        return {"ok": False, "error": "Training failed.", "data": {"steps": steps}}

    dlpk = (trained.get("data") or {}).get("model_package")
    if not dlpk:
        return {"ok": False, "error": "Training produced no .dlpk.", "data": {"steps": steps}}

    detected = detect_objects(in_raster, out_features, dlpk)
    steps.append({"step": "detect_objects", "ok": detected.get("ok"),
                  **(detected.get("data") or {"error": detected.get("error")})})
    if not detected.get("ok"):
        return {"ok": False, "error": "Inference failed.", "data": {"steps": steps}}

    return ok(
        {
            "raster": in_raster,
            "model_package": dlpk,
            "output": out_features,
            "steps": steps,
            "feature_count": (detected.get("data") or {}).get("feature_count"),
        }
    )


# --- helpers ---------------------------------------------------------------

def _format_arguments(arguments: dict) -> str | None:
    """ArcGIS deep-learning tools take arguments as a 'k value;k value' string."""
    if not arguments:
        return None
    return ";".join(f"{k} {v}" for k, v in arguments.items())


def _messages(result: Any) -> str | None:
    try:
        return result.getMessages()
    except Exception:  # noqa: BLE001
        return None


class _processor:
    """Context manager pinning arcpy.env.processorType for one inference run.

    Restores the previous value afterwards so a CPU fallback for one call does
    not silently slow down every later call in the same server process.
    """

    def __init__(self, arcpy, processor_type: str | None):
        self.arcpy = arcpy
        self.requested = processor_type.upper() if processor_type else None
        self.previous = None

    def __enter__(self):
        if self.requested:
            self.previous = self.arcpy.env.processorType
            self.arcpy.env.processorType = self.requested
        return self

    def __exit__(self, *exc):
        if self.requested:
            self.arcpy.env.processorType = self.previous
        return False
