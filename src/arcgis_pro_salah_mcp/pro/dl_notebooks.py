"""Author ArcGIS Pro deep-learning notebooks: interview first, then a runnable script.

The workflow this module serves
-------------------------------
A user says "I want a notebook that detects buildings" (or "classifies objects").
The agent must NOT guess batch size, epochs or learning rate — those depend on the
user's GPU and data. So the flow, through the single MCP tool :func:`notebook`, is:

1. Called with just the goal, it recommends a model and replies ``status=ask_user``
   with the questions to put to the user. Plain "classification" is ambiguous (pixels?
   objects? find-and-label?) so it replies ``status=choose`` first.
2. The agent asks them in chat and waits for the answers.
3. Called again with the answers, it writes an ``.ipynb`` whose cells follow the same
   steps as a hand-written ``arcgis.learn`` notebook — ``prepare_data`` -> model ->
   ``lr_find`` -> ``fit`` -> metrics -> ``show_results`` -> ``save`` — one step per
   cell, so the user can run it cell by cell inside ArcGIS Pro.

It refuses to write until the required answers exist and says which are missing, so
skipping the interview is impossible rather than merely discouraged.

Speed
-----
Writing the notebook takes ~5 ms (32 cells, compiled and saved) — that is not where
time goes. What costs is round trips and the tool schema sent on every request, so the
whole feature is ONE tool, and the output location is looked up from the live bridge
(the open project's folder) instead of costing the agent another call.

Pure Python
-----------
No ``arcpy`` and no ``arcgis`` import: the notebook is written as JSON by hand, so the
module is unit-testable with no ArcGIS installed. Every constructor/`fit` argument it
emits was read from the installed ``arcgis.learn`` (API 2.4.3) with
``inspect.signature`` — see :data:`MODELS`.

The generated file mirrors the notebooks Pro itself writes (nbformat 4.4, no per-cell
ids, kernelspec name ``python3`` / display name ``ArcGISPro``).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .._result import err, guard, ok
from .register import bridge_listening as _bridge_listening

_NB_FORMAT, _NB_MINOR = 4, 4
_NB_METADATA = {
    "kernelspec": {"display_name": "ArcGISPro", "language": "python", "name": "python3"},
    "language_info": {
        "file_extension": ".py",
        "mimetype": "text/x-python",
        "name": "python",
        "version": "3.13.5",
    },
}

# ---------------------------------------------------------------------------
# Model registry.
#
# ``init_args`` is the set of OPTIONAL constructor keywords the installed
# arcgis.learn accepts for that class (read with inspect.signature). Anything else
# is refused instead of emitting a call that fails after a two-minute import.
# ``formats`` are the Export Training Data metadata formats the class supports.
# ``metrics`` are methods that exist on the class — SamLoRA has none, and
# FeatureClassifier only has plot_confusion_matrix.
# ---------------------------------------------------------------------------
_DETECTION_FORMATS = ["PASCAL_VOC_rectangles", "KITTI_rectangles"]
_AP = ["average_precision_score"]
_SEG = ["accuracy", "per_class_metrics", "mIOU"]

MODELS: dict[str, dict[str, Any]] = {
    "maskrcnn": {
        "cls": "MaskRCNN", "label": "Mask R-CNN", "task": "instance_segmentation",
        "formats": ["RCNN_Masks"], "metrics": _AP,
        "init_args": ["backbone", "pointrend", "pretrained_path"],
        "good_for": "Every building as its own polygon mask — what the course notebook trains.",
        "trade_off": "Heaviest of the common models: slowest epochs, most GPU memory.",
    },
    "unet": {
        "cls": "UnetClassifier", "label": "U-Net", "task": "pixel_classification",
        "formats": ["Classified_Tiles"], "metrics": _SEG,
        "init_args": ["backbone", "pretrained_path"],
        "good_for": "Classify every pixel (land cover) or footprints as a raster mask; "
                    "Esri's overview page lists footprint extraction as its example.",
        "trade_off": "Touching buildings merge into one blob (no per-building instances).",
    },
    "deeplab": {
        "cls": "DeepLab", "label": "DeepLab", "task": "pixel_classification",
        "formats": ["Classified_Tiles"], "metrics": _SEG,
        "init_args": ["backbone", "pointrend", "pretrained_path"],
        "good_for": "Pixel classification with strong context handling.",
        "trade_off": "Same instance-merging limit as U-Net.",
    },
    "pspnet": {
        "cls": "PSPNetClassifier", "label": "PSPNet", "task": "pixel_classification",
        "formats": ["Classified_Tiles"], "metrics": _SEG,
        "init_args": ["backbone", "pointrend", "use_unet", "pretrained_path"],
        "good_for": "Pixel classification using multi-scale pooling.",
        "trade_off": "Same instance-merging limit as U-Net.",
    },
    "featureclassifier": {
        "cls": "FeatureClassifier", "label": "Feature Classifier", "task": "object_classification",
        "formats": ["Labeled_Tiles", "MultiLabeled_Tiles", "Imagenet"],
        "metrics": ["plot_confusion_matrix"],
        "init_args": ["backbone", "mixup", "oversample", "pretrained_path"],
        "good_for": "Give each EXISTING object (a building polygon, a point) a class — roof "
                    "type, damaged/intact, land-use of a parcel. You label examples; it "
                    "learns to label the rest.",
        "trade_off": "It does not FIND objects: the features to classify must already exist.",
    },
    "fasterrcnn": {
        "cls": "FasterRCNN", "label": "Faster R-CNN", "task": "object_detection",
        "formats": _DETECTION_FORMATS, "metrics": _AP,
        "init_args": ["backbone", "pretrained_path"],
        "good_for": "Bounding boxes around buildings — cheaper labels than masks.",
        "trade_off": "Boxes, not footprints.",
    },
    "retinanet": {
        "cls": "RetinaNet", "label": "RetinaNet", "task": "object_detection",
        "formats": _DETECTION_FORMATS, "metrics": _AP,
        "init_args": ["backbone", "pretrained_path"],
        "good_for": "Bounding boxes; copes well with many small, dense objects.",
        "trade_off": "Boxes, not footprints.",
    },
    "ssd": {
        "cls": "SingleShotDetector", "label": "SSD", "task": "object_detection",
        "formats": _DETECTION_FORMATS, "metrics": _AP,
        "init_args": ["backbone", "pretrained_path"],
        "good_for": "Fast bounding-box detector.",
        "trade_off": "Boxes, not footprints; weaker on very small objects.",
    },
    "yolov3": {
        "cls": "YOLOv3", "label": "YOLOv3", "task": "object_detection",
        "formats": _DETECTION_FORMATS, "metrics": _AP,
        "init_args": ["pretrained_path"],
        "good_for": "Fast bounding-box detector (no backbone choice).",
        "trade_off": "Boxes, not footprints.",
    },
    "detreg": {
        "cls": "DETReg", "label": "DETReg", "task": "object_detection",
        "formats": _DETECTION_FORMATS, "metrics": _AP,
        "init_args": ["backbone", "pretrained_path"],
        "good_for": "Transformer detector.",
        "trade_off": "Boxes, not footprints; transformers are slow to converge.",
    },
    "rtdetrv2": {
        "cls": "RTDetrV2", "label": "RT-DETR v2", "task": "object_detection",
        "formats": _DETECTION_FORMATS, "metrics": _AP,
        "init_args": ["backbone", "pretrained_path"],
        "good_for": "Real-time transformer detector.",
        "trade_off": "Boxes, not footprints.",
    },
    "samlora": {
        "cls": "SamLoRA", "label": "SAM + LoRA", "task": "pixel_classification",
        "formats": ["Classified_Tiles"], "metrics": [],
        "init_args": ["backbone", "pretrained_path"],
        "good_for": "Fine-tunes Segment Anything on your labels with few trainable weights.",
        "trade_off": "arcgis.learn gives it no accuracy/mIOU method — judge it with "
                     "show_results. Not exercised here.",
    },
    "changedetector": {
        "cls": "ChangeDetector", "label": "Change Detector", "task": "change_detection",
        "formats": ["Classified_Tiles"], "metrics": ["precision_recall_score"],
        "init_args": ["backbone", "attention_type", "pretrained_path"],
        "data_note": "For change detection export with the SECOND-date raster (in_raster2), "
                     "so the folder has images, images2 and labels; prepare_data then "
                     "detects it as ChangeDetection by itself.",
        "good_for": "Learn YOUR kind of change (new buildings, cleared land, flooding...) "
                    "from before/after chip pairs you label.",
        "trade_off": "Needs paired, accurately aligned before/after chips with change labels.",
    },
}

# Ready-made Esri models: no training, so no batch size / epochs / learning rate. Every
# fact below was read from the public item page (a779106c...), not remembered.
PRETRAINED: dict[str, dict[str, Any]] = {
    "esri_building_change": {
        "label": "Esri Building Change Detection", "task": "change_detection",
        "item_id": "a779106ce9eb4fc28de6d04d8edb893c", "size_mb": 93,
        "tool": "DetectChangeUsingDeepLearning", "cell_size_m": (0.10, 0.20),
        "good_for": "Detects changed buildings between two dates of very-high-resolution "
                    "imagery, with no training.",
        "heads_up": [
            "Needs 10-20 cm aerial or drone imagery for BOTH dates, accurately aligned with "
            "no relief displacement.",
            "The output is a continuous change-MAGNITUDE raster (a probability per pixel), "
            "not a binary map.",
            "Expected to work best in less densely populated urban areas.",
            "Esri says it cannot be fine-tuned with ArcGIS tools, so there are no epochs or "
            "learning rate to choose.",
            "The item is flagged 'Requires Subscription' and licensed under the Esri Master "
            "License Agreement - check you are entitled to it.",
        ],
    },
}
_PRETRAINED_OPTIONS = {"processor": ("CPU", "GPU"), "output_name": None, "model_folder": None}
_OPTION_CHOICES = {"attention_type": ("PAM", "BAM")}

# The Image Analyst tool that applies each kind of trained model.
TASK_INFERENCE = {
    "instance_segmentation": "DetectObjectsUsingDeepLearning",
    "object_detection": "DetectObjectsUsingDeepLearning",
    "pixel_classification": "ClassifyPixelsUsingDeepLearning",
    "object_classification": "ClassifyObjectsUsingDeepLearning",
    "change_detection": "DetectChangeUsingDeepLearning",
}

_ALTERNATIVES = {
    "maskrcnn": ["unet", "fasterrcnn"],
    "unet": ["deeplab", "pspnet", "maskrcnn"],
    "deeplab": ["unet", "pspnet"],
    "featureclassifier": [],
    "fasterrcnn": ["retinanet", "ssd", "maskrcnn"],
    "changedetector": [],
}

# Written into TRAINING_DATA only when the user explicitly chose to type the path
# themselves (create_training_notebook(data_path_later=True)).
_DATA_PLACEHOLDER = "<PUT THE PATH TO YOUR EXPORTED TRAINING DATA HERE>"

_HOW_TO_ASK = (
    "Ask the user these questions in ONE message, showing each default. Wait for their "
    "answers. Do NOT fill in required answers yourself. Then call the tool again with them."
)


def _base_questions(model: dict | None) -> list[dict]:
    """The interview. ``required`` ones have no safe default."""
    init = model["init_args"] if model else []
    q: list[dict] = [
        {"key": "training_data", "required": True, "default": None,
         "question": "Path to the exported training-data folder (output of Export Training "
                     "Data For Deep Learning)?",
         "why": "prepare_data reads chips + labels from it. Its metadata format must match "
                "the model" + (f" ({', '.join(model['formats'])}). "
                               + model.get("data_note", "") if model else ".")},
        {"key": "batch_size", "required": True, "default": None,
         "question": "Batch size?",
         "why": "Chips per training step. Bigger = faster epochs but more GPU memory; if you "
                "hit out-of-memory, halve it. arcgis.learn's own default is 64, which is "
                "often far too large for detection models; the course notebook used 4."},
        {"key": "epochs", "required": True, "default": None,
         "question": "Number of epochs?",
         "why": "Full passes over the training chips. The course notebook used 30 at ~3 min "
                "each. Consider 2-3 first just to prove the pipeline end to end."},
        {"key": "learning_rate", "required": False, "default": "auto (lr_find)",
         "question": "Learning rate — let lr_find() pick it, or give a number?",
         "why": "The single most influential hyper-parameter; lr_find() plots loss against "
                "rate and suggests one."},
        {"key": "chip_size", "required": False, "default": "library default (224)",
         "question": "Chip size?",
         "why": "Normally the tile size you exported with (the course notebook used 400)."},
        {"key": "val_split_pct", "required": False, "default": 0.1,
         "question": "Fraction held out for validation?",
         "why": "Validation loss/metrics are measured on it. 0.1 = 10 %."},
    ]
    if "backbone" in init:
        q.append({"key": "backbone", "required": False, "default": "model default",
                  "question": "Backbone network (leave blank for the model's default)?",
                  "why": "Bigger backbones (e.g. resnet101) can be more accurate but are "
                         "slower and need more memory."})
    if "pointrend" in init:
        q.append({"key": "pointrend", "required": False, "default": False,
                  "question": "Use the PointRend head?",
                  "why": "Sharper mask edges (better footprints), slower and heavier. The "
                         "course notebook used True."})
    if "oversample" in init:
        q.append({"key": "oversample", "required": False, "default": False,
                  "question": "Oversample rare classes?",
                  "why": "Helps when some classes have far fewer examples than others."})
    if "mixup" in init:
        q.append({"key": "mixup", "required": False, "default": False,
                  "question": "Use mixup augmentation?",
                  "why": "Blends pairs of chips; often reduces over-fitting on small datasets."})
    if "attention_type" in init:
        q.append({"key": "attention_type", "required": False, "default": "PAM",
                  "question": "Attention module: PAM (pyramid, the default) or BAM (basic)?",
                  "why": "Controls how the two dates are compared; PAM is the library default."})
    q += [
        {"key": "mixed_precision", "required": False, "default": False,
         "question": "Use mixed precision (needs an NVIDIA GPU)?",
         "why": "Less memory and faster steps on supported GPUs; it is ignored/unsafe on CPU."},
        {"key": "early_stopping", "required": False, "default": False,
         "question": "Stop early when validation loss stops improving?",
         "why": "Saves time when you over-estimate the epochs."},
        {"key": "model_name", "required": False, "default": "derived from model + epochs",
         "question": "Name to save the trained model under?",
         "why": "The folder (and .dlpk) is created under <training_data>/models/."},
        {"key": "input_raster", "required": False, "default": None,
         "question": "Image to run the trained model on afterwards (optional)?",
         "why": "If given, the notebook ends with an inference cell; otherwise it is left "
                "as a template."},
    ]
    if model and model["task"] == "object_classification":
        q.append({"key": "input_features", "required": False, "default": None,
                  "question": "Features to classify afterwards (polygons/points, optional)?",
                  "why": "ClassifyObjectsUsingDeepLearning labels these; without them the "
                         "last cell is a template."})
    return q


def _questions(model: dict | None) -> list[dict]:
    """The interview, adjusted for the task."""
    q = _base_questions(model)
    if model and model["task"] == "change_detection":
        for item in q:
            if item["key"] == "input_raster":
                item["question"] = "BEFORE-date image to run the trained model on afterwards (optional)?"
        q.append({"key": "second_raster", "required": False, "default": None,
                  "question": "AFTER-date image (optional)?",
                  "why": "Change detection compares two dates; without both, the last cell is "
                         "a template."})
    return q


def _pretrained_questions(entry: dict) -> list[dict]:
    """Ready-made models have no training, so only inputs and inference settings."""
    return [
        {"key": "input_raster", "required": True, "default": None,
         "question": "BEFORE-date image (path to the raster or mosaic dataset)?",
         "why": "First of the two dates. " + entry["heads_up"][0]},
        {"key": "second_raster", "required": True, "default": None,
         "question": "AFTER-date image?",
         "why": "Second date. It must align with the first (same area, no relief displacement)."},
        {"key": "batch_size", "required": False, "default": "model default",
         "question": "Inference batch size?",
         "why": "Tiles processed at once; lower it if the GPU runs out of memory. This is "
                "NOT a training batch size - there is no training."},
        {"key": "processor", "required": False, "default": "ArcGIS decides",
         "question": "Run on the GPU or the CPU?",
         "why": "Sets arcpy.env.processorType. CPU works but is much slower."},
        {"key": "output_name", "required": False, "default": "building_change",
         "question": "Name for the output raster?",
         "why": "Saved in the project's default geodatabase."},
    ]


# ---------------------------------------------------------------------------
# Routing: what does the user actually mean?
# ---------------------------------------------------------------------------
_CLS_WORDS = ("classif", "categoriz", "categoris", "كلاسيفيكيشن", "تصنيف", "صنف", "صنّف")
_PIXEL_WORDS = ("pixel", "land cover", "landcover", "land-cover", "land use", "landuse",
                "semantic", "بكسل")
_OBJECT_WORDS = ("object", "feature", "each ", "every ", "per building", "per feature",
                 "roof", "damage", "اوبجيكت", "أوبجيكت", "كائن")
_CHANGE_WORDS = ("change", "تغي")
_PRETRAINED_WORDS = ("esri", "pretrained", "pre-trained", "ready-made", "ready made",
                     "ready to use", "ايزري", "إيزري", "جاهز")


def _route(goal: str, prefer: str | None) -> tuple[str, str | None, str | None]:
    """Return (mode, model_key, reason).

    mode is 'train', 'pretrained', or 'choose' (reason then names which question to ask).
    """
    text = (goal or "").lower()
    if prefer:
        p = prefer.lower()
        if p in PRETRAINED:
            return "pretrained", p, f"You asked for {PRETRAINED[p]['label']}."
        if p in MODELS:
            return "train", p, f"You asked for {MODELS[p]['label']}."

    # Change detection is checked FIRST: it used to fall through to Mask R-CNN.
    if any(w in text for w in _CHANGE_WORDS):
        if any(w in text for w in _PRETRAINED_WORDS):
            return "pretrained", "esri_building_change", (
                "You asked for Esri's ready-made model, which detects changed buildings "
                "between two dates with no training.")
        return "choose", None, "change"

    has_cls = any(w in text for w in _CLS_WORDS)
    has_pixel = any(w in text for w in _PIXEL_WORDS)
    has_object = any(w in text for w in _OBJECT_WORDS)

    if has_cls and has_pixel:
        return "train", "unet", ("You want every pixel labelled (land cover / land use). "
                                 "U-Net is the standard starting point.")
    if has_cls and has_object:
        return "train", "featureclassifier", (
            "You want each existing object given a class. Feature Classifier labels "
            "objects (it does not find them).")
    if has_cls:
        return "choose", None, "classification"
    if has_pixel:
        return "train", "unet", "Pixel-level labelling: U-Net is the standard starting point."
    if any(w in text for w in ("box", "bounding", "count")):
        return "train", "fasterrcnn", "You only need boxes/counts, which is cheaper than masks."
    if any(w in text for w in ("footprint", "segment", "mask", "polygon", "raster", "instance")):
        return "train", "maskrcnn", ("Per-building polygons (footprints). Mask R-CNN keeps each "
                                     "building separate; U-Net is lighter but merges neighbours.")
    return "train", "maskrcnn", ("Buildings are usually wanted as separate shapes, which is "
                                 "what the course notebook already trains.")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
@guard
def list_models(task: str | None = None) -> dict:
    """Trainable models by task, with the metadata format each needs. Pure lookup."""
    rows = [
        {"key": k, "class": v["cls"], "label": v["label"], "task": v["task"],
         "formats": v["formats"], "good_for": v["good_for"], "trade_off": v["trade_off"]}
        for k, v in MODELS.items()
        if task is None or v["task"] == task.strip().lower()
    ]
    if task and not rows:
        return err(f"Unknown task '{task}'.",
                   valid=sorted({v["task"] for v in MODELS.values()}))
    return ok({
        "models": rows,
        "pretrained": [
            {"key": k, "label": v["label"], "task": v["task"], "item_id": v["item_id"],
             "size_mb": v["size_mb"], "good_for": v["good_for"], "heads_up": v["heads_up"]}
            for k, v in PRETRAINED.items()
            if task is None or v["task"] == task.strip().lower()
        ],
        "not_included": "MMDetection / MMSegmentation need a mandatory `model` name and "
                        "are left out of this version.",
    })


_CHOICES = {
    "classification": {
        "question": "Which kind of classification do you mean?",
        "choices": [
            {"key": "pixel", "means": "label EVERY PIXEL (land cover, land use)",
             "recommended": "unet", "alternatives": ["deeplab", "pspnet"]},
            {"key": "object", "means": "give each EXISTING object/feature a class "
                                       "(e.g. the roof type of each building)",
             "recommended": "featureclassifier"},
            {"key": "detect", "means": "FIND the objects and label their class",
             "recommended": "maskrcnn", "alternatives": ["fasterrcnn", "retinanet"]},
        ],
    },
    "change": {
        "question": "Esri's ready-made change-detection model, or train your own?",
        "choices": [
            {"key": "pretrained", "recommended": "esri_building_change",
             "means": "Use Esri's ready-made Building Change Detection: no training (so no "
                      "epochs), buildings only, needs 10-20 cm aligned imagery for both dates, "
                      "and the item is flagged 'Requires Subscription'."},
            {"key": "train", "recommended": "changedetector",
             "means": "Train your own ChangeDetector on labelled before/after chip pairs: any "
                      "kind of change, but you must label the data."},
        ],
    },
}


@guard
def plan(goal: str = "detect buildings", prefer: str | None = None) -> dict:
    """Recommend a model and return the interview questions. Ask them; do not guess."""
    mode, key, reason = _route(goal, prefer)
    if mode == "choose":
        spec = _CHOICES[reason]
        return ok({"mode": "choose", **spec,
                   "how_to_ask": "Ask the user which one, then call again with "
                                 "model=<the recommended key>."})

    if mode == "pretrained":
        entry = PRETRAINED[key]
        return ok({
            "mode": "pretrained", "recommended": key, "label": entry["label"],
            "task": entry["task"], "reason": reason, "heads_up": entry["heads_up"],
            "alternatives": [],
            "questions": _pretrained_questions(entry),
            "how_to_ask": _HOW_TO_ASK,
            "next_tool": "pro_dl_notebook",
        })

    cfg = MODELS[key]
    return ok({
        "mode": "train",
        "recommended": key,
        "label": cfg["label"],
        "task": cfg["task"],
        "formats": cfg["formats"],
        "reason": reason,
        "alternatives": [
            {"key": k, "label": MODELS[k]["label"], "why": MODELS[k]["good_for"],
             "trade_off": MODELS[k]["trade_off"]}
            for k in _ALTERNATIVES.get(key, [])
        ],
        "questions": _questions(cfg),
        "how_to_ask": _HOW_TO_ASK,
        "next_tool": "pro_dl_notebook",
    })


# ---------------------------------------------------------------------------
# Notebook assembly
# ---------------------------------------------------------------------------
def _lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True) if text else []


def _md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": _lines(text)}


def _code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": _lines(text)}


def _path(p: str) -> str:
    """A Python literal for a path: raw string when that is safe, else repr."""
    p = str(p)
    if '"' not in p and "\n" not in p and not p.endswith("\\"):
        return f'r"{p}"'
    return repr(p)


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", text).strip("_") or "model"


def _env_cell() -> str:
    return (
        "import sys, torch, arcgis\n"
        "print('python   :', sys.version.split()[0])\n"
        "print('arcgis   :', arcgis.__version__)\n"
        "print('torch    :', torch.__version__)\n"
        "print('CUDA GPU :', torch.cuda.is_available(),\n"
        "      torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')\n"
        "if not torch.cuda.is_available():\n"
        "    print('\\nWARNING: no CUDA GPU. Training will be very slow - use a tiny EPOCHS '\n"
        "          'to check the pipeline, or run this notebook on a GPU machine.')\n"
    )


def _validate_cells(cells: list[dict], path: str) -> str | None:
    """compile() every code cell; return a message for the first that fails."""
    for i, cell in enumerate(cells):
        if cell["cell_type"] != "code":
            continue
        try:
            compile("".join(cell["source"]), f"{os.path.basename(path)} cell {i}", "exec")
        except SyntaxError as exc:
            return f"cell {i}: {exc.msg} (line {exc.lineno})"
    return None


def _write(cells: list[dict], out_path: str, overwrite: bool) -> dict | None:
    if os.path.exists(out_path) and not overwrite:
        return err(f"{out_path} already exists (pass overwrite=True).", code="exists")
    problem = _validate_cells(cells, out_path)
    if problem:
        return err(f"Generated notebook has a syntax error in {problem}.", code="invalid_source")
    nb = {"cells": cells, "metadata": _NB_METADATA,
          "nbformat": _NB_FORMAT, "nbformat_minor": _NB_MINOR}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(nb, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return None


_ADD_TO_PRO = (
    "A notebook file is NOT picked up automatically. In ArcGIS Pro open the Catalog pane, "
    "right-click Notebooks and choose 'Add and Open Notebook' (it is also in the New "
    "Notebook split button), then pick this file. Run the cells top to bottom, one at a time."
)


def _outline(cells: list[dict]) -> list[dict]:
    out = []
    for i, c in enumerate(cells):
        if c["cell_type"] == "markdown":
            first = "".join(c["source"]).splitlines()[0] if c["source"] else ""
            out.append({"cell": i, "type": "markdown", "title": first.lstrip("# ").strip()})
        else:
            out.append({"cell": i, "type": "code",
                        "first_line": ("".join(c["source"]).splitlines() or [""])[0][:70]})
    return out


def _needs(missing: list[str], model: dict | None) -> dict:
    qs = [q for q in _questions(model) if q["key"] in missing]
    return err(
        "Missing answers: " + ", ".join(missing) + ". Ask the user — do not guess these.",
        code="needs_answers", missing=missing, questions=qs,
    )


def _option_literal(key: str, value: Any) -> str:
    return _path(value) if key == "pretrained_path" else repr(value)


def _metric_cell(cfg: dict) -> tuple[str, str]:
    if "average_precision_score" in cfg["metrics"]:
        return ("model.average_precision_score()",
                "Average precision per class on the validation chips.")
    if "accuracy" in cfg["metrics"]:
        return ("model.accuracy()\nmodel.per_class_metrics()\nmodel.mIOU()",
                "Pixel accuracy, per-class precision/recall/F1, and mean IoU.")
    if "precision_recall_score" in cfg["metrics"]:
        return ("model.precision_recall_score()",
                "Precision, recall and F1 score for the change class.")
    if "plot_confusion_matrix" in cfg["metrics"]:
        return ("model.plot_confusion_matrix()",
                "Confusion matrix: which classes get mistaken for which.")
    return ("# arcgis.learn gives this model no accuracy / mIOU method.\n"
            "# Judge it visually in the next cell.",
            "This model exposes no metric method; use the visual check below.")


def _inference_cells(cfg: dict, input_raster: str | None, input_features: str | None,
                     second_raster: str | None = None) -> list[dict]:
    fn = TASK_INFERENCE[cfg["task"]]
    changing = fn == "DetectChangeUsingDeepLearning"
    raster = (_path(input_raster) if input_raster else
              ('"<path to the BEFORE-date image>"' if changing
               else '"<path to the image to run the model on>"'))
    setup = (
        "import arcpy\nfrom pathlib import Path\n"
        "arcpy.CheckOutExtension('ImageAnalyst')\n"
        f"INPUT_RASTER = {raster}\n"
    )
    if changing:
        second = _path(second_raster) if second_raster else '"<path to the AFTER-date image>"'
        setup += f"SECOND_RASTER = {second}\n"
    if fn == "ClassifyObjectsUsingDeepLearning":
        feats = _path(input_features) if input_features else '"<features to classify>"'
        setup += f"INPUT_FEATURES = {feats}\n"
    setup += (
        "DLPK = next(Path(model_path).glob('*.dlpk'))\n"
        "try:\n"
        "    DEFAULT_GDB = arcpy.mp.ArcGISProject('CURRENT').defaultGeodatabase\n"
        "except OSError:  # not running inside ArcGIS Pro (seen when run headless)\n"
        "    DEFAULT_GDB = arcpy.env.scratchGDB\n"
        "print('model package:', DLPK)\n"
        "print('outputs go to:', DEFAULT_GDB)"
    )
    if fn == "DetectObjectsUsingDeepLearning":
        run = ('OUT_FEATURES = f"{DEFAULT_GDB}\\\\{MODEL_NAME}_detections"\n'
               "arcpy.ia.DetectObjectsUsingDeepLearning(\n"
               "    INPUT_RASTER, OUT_FEATURES, str(DLPK),\n"
               "    arguments=f'batch_size {BATCH_SIZE}',\n"
               "    run_nms='NMS', confidence_score_field='Confidence',\n"
               "    class_value_field='Class', max_overlap_ratio=0,\n"
               "    processing_mode='PROCESS_AS_MOSAICKED_IMAGE')\n"
               "print('wrote', OUT_FEATURES)")
    elif fn == "ClassifyObjectsUsingDeepLearning":
        run = ('OUT_FEATURES = f"{DEFAULT_GDB}\\\\{MODEL_NAME}_classified"\n'
               "arcpy.ia.ClassifyObjectsUsingDeepLearning(\n"
               "    in_raster=INPUT_RASTER, out_feature_class=OUT_FEATURES,\n"
               "    in_model_definition=str(DLPK), in_features=INPUT_FEATURES,\n"
               "    class_label_field='Label',\n"
               "    processing_mode='PROCESS_AS_MOSAICKED_IMAGE',\n"
               "    model_arguments=f'batch_size {BATCH_SIZE}')\n"
               "print('wrote', OUT_FEATURES)")
    elif changing:
        run = ('OUT_RASTER = f"{DEFAULT_GDB}\\\\{MODEL_NAME}_change"\n'
               "change = arcpy.ia.DetectChangeUsingDeepLearning(\n"
               "    INPUT_RASTER, SECOND_RASTER, str(DLPK), f'batch_size {BATCH_SIZE}')\n"
               "change.save(OUT_RASTER)\n"
               "print('wrote', OUT_RASTER)")
    else:
        run = ('OUT_RASTER = f"{DEFAULT_GDB}\\\\{MODEL_NAME}_classified"\n'
               "classified = arcpy.ia.ClassifyPixelsUsingDeepLearning(\n"
               "    INPUT_RASTER, str(DLPK), arguments=f'batch_size {BATCH_SIZE}',\n"
               "    processing_mode='PROCESS_AS_MOSAICKED_IMAGE')\n"
               "classified.save(OUT_RASTER)\n"
               "print('wrote', OUT_RASTER)")
    return [
        _md("## 15. Use the trained model on imagery (optional)\n"
            "Needs the Image Analyst extension. **This cell was generated from Esri's tool "
            "signatures but not executed here** — expect to adjust `arguments` for your model."),
        _code(setup),
        _code(run),
    ]


@guard
def create_training_notebook(
    model: str,
    out_path: str,
    training_data: str | None = None,
    batch_size: int | None = None,
    epochs: int | None = None,
    learning_rate: float | None = None,
    chip_size: int | None = None,
    val_split_pct: float | None = None,
    options: dict | None = None,
    mixed_precision: bool = False,
    early_stopping: bool = False,
    model_name: str | None = None,
    input_raster: str | None = None,
    input_features: str | None = None,
    second_raster: str | None = None,
    title: str | None = None,
    overwrite: bool = False,
    data_path_later: bool = False,
) -> dict:
    """Write a cell-by-cell training notebook for one arcgis.learn model.

    ``options`` are model-specific constructor keywords (backbone, pointrend, mixup,
    oversample, ...). Only keywords the installed class accepts are allowed.

    ``data_path_later=True`` is for the case where the USER explicitly said they will
    type the training-data path themselves: the notebook then carries a clearly marked
    placeholder and refuses to load data until it is replaced. Never set it on your own
    initiative — the path is exactly the kind of answer that must not be guessed.
    """
    key = (model or "").strip().lower()
    if key not in MODELS:
        return err(f"Unknown model '{model}'.", valid=sorted(MODELS), code="unknown_model")
    cfg = MODELS[key]

    placeholder_used = False
    if data_path_later and training_data in (None, ""):
        training_data, placeholder_used = _DATA_PLACEHOLDER, True

    missing = [k for k, v in (("training_data", training_data), ("batch_size", batch_size),
                              ("epochs", epochs)) if v in (None, "")]
    if missing:
        return _needs(missing, cfg)

    if not str(out_path).lower().endswith(".ipynb"):
        out_path = f"{out_path}.ipynb"
    if int(batch_size) < 1 or int(epochs) < 1:
        return err("batch_size and epochs must be at least 1.", code="bad_value")
    if chip_size is not None and int(chip_size) < 1:
        return err("chip_size must be positive.", code="bad_value")
    if learning_rate is not None and not float(learning_rate) > 0:
        return err("learning_rate must be a positive number.", code="bad_value")
    if val_split_pct is not None and not 0 < float(val_split_pct) < 1:
        return err("val_split_pct must be between 0 and 1.", code="bad_value")

    options = dict(options or {})
    for k, v in options.items():
        if k not in cfg["init_args"]:
            return err(f"{cfg['label']} has no '{k}' option. Valid: {cfg['init_args']}.",
                       code="unsupported_option")
        if not isinstance(v, (bool, int, float, str)):
            return err(f"Option '{k}' must be a bool, number or string.", code="bad_value")
        if k in _OPTION_CHOICES and v not in _OPTION_CHOICES[k]:
            return err(f"Option '{k}' must be one of {list(_OPTION_CHOICES[k])}.", code="bad_value")

    name = model_name or f"{_slug(cfg['label']).lower()}_{int(epochs)}ep"
    ctor = f"{cfg['cls']}(data" + "".join(f", {k}={k.upper()}" for k in options) + ")"

    params = [
        f"TRAINING_DATA = {_path(training_data)}",
        f"CHIP_SIZE      = {int(chip_size) if chip_size else None}"
        "     # None -> arcgis.learn default (224); usually = the tile size you exported",
        f"BATCH_SIZE     = {int(batch_size)}",
        f"VAL_SPLIT_PCT  = {float(val_split_pct) if val_split_pct is not None else 0.1}",
        f"EPOCHS         = {int(epochs)}",
        f"LEARNING_RATE  = {float(learning_rate) if learning_rate else None}"
        "     # None -> model.lr_find() picks one",
        f"MIXED_PRECISION = {bool(mixed_precision)}   # needs an NVIDIA GPU",
        f"EARLY_STOPPING  = {bool(early_stopping)}",
    ]
    params += [f"{k.upper():<15}= {_option_literal(k, v)}" for k, v in options.items()]
    params.append(f"MODEL_NAME     = {name!r}")

    metric_src, metric_note = _metric_cell(cfg)

    cells: list[dict] = [
        _md(f"# {title or cfg['label'] + ' - training notebook'}\n\n"
            f"Generated by Salah MCP. Model: **{cfg['label']}** (`arcgis.learn.{cfg['cls']}`), "
            f"task: {cfg['task']}, training format: {', '.join(cfg['formats'])}.\n\n"
            "Run the cells **one at a time, top to bottom**. Change the values in the "
            "*Parameters* cell and re-run from there."),
        _md("## 1. Environment check"),
        _code(_env_cell()),
        _md("## 2. Imports"),
        _code(f"from arcgis.learn import {cfg['cls']}, prepare_data\n"
              "# note: this import is slow the first time (measured ~2 min on a cold machine)"),
        _md("## 3. Parameters\nEverything you were asked about lives here."),
        _code("\n".join(params)),
        _md("## 4. Load the training data"),
        _code("import os\n"
              "if not os.path.isdir(TRAINING_DATA):\n"
              "    raise FileNotFoundError(\n"
              "        f'Set TRAINING_DATA in the Parameters cell first (it is {TRAINING_DATA!r}).')\n"
              "prepare_kwargs = dict(batch_size=BATCH_SIZE, val_split_pct=VAL_SPLIT_PCT)\n"
              "if CHIP_SIZE:\n"
              "    prepare_kwargs['chip_size'] = CHIP_SIZE\n"
              "data = prepare_data(TRAINING_DATA, **prepare_kwargs)"),
        _md("## 5. Classes found"),
        _code("data.classes"),
        _md("## 6. Look at a batch\nCheck the labels line up with the imagery before training."),
        _code("data.show_batch()"),
        _md(f"## 7. Create the model\nExpects `{' / '.join(cfg['formats'])}` data."),
        _code(f"model = {ctor}"),
        _md("## 8. Sanity check the data format"),
        _code("model.supported_datasets"),
        _md("## 9. Learning rate\nUses `LEARNING_RATE` if you set one, otherwise `lr_find()`."),
        _code("lr = LEARNING_RATE if LEARNING_RATE else model.lr_find()\nlr"),
        _md("## 10. Train\n**Long-running.** Progress prints per epoch."),
        _code("model.fit(EPOCHS, lr=lr, early_stopping=EARLY_STOPPING,\n"
              "          mixed_precision=MIXED_PRECISION)"),
        _md("## 11. Training curves"),
        _code("model.plot_losses()"),
        _md(f"## 12. Metrics\n{metric_note}"),
        _code(metric_src),
        _md("## 13. Predictions vs ground truth"),
        _code("model.show_results()"),
        _md("## 14. Save the trained model\nWrites a folder under `<training_data>/models/` "
            "containing the `.dlpk` that Pro's inference tools use."),
        _code("model_path = model.save(MODEL_NAME)\nmodel_path"),
    ]
    cells += _inference_cells(cfg, input_raster, input_features, second_raster)

    failure = _write(cells, out_path, overwrite)
    if failure:
        return failure
    return ok({
        "status": "created",
        "notebook": os.path.abspath(out_path),
        "model": cfg["cls"], "task": cfg["task"], "model_name": name,
        "data_path_placeholder": placeholder_used,
        "cells": len(cells), "outline": _outline(cells),
        "answers_used": {"batch_size": int(batch_size), "epochs": int(epochs),
                         "chip_size": chip_size, "learning_rate": learning_rate,
                         "val_split_pct": val_split_pct, "options": options,
                         "mixed_precision": mixed_precision,
                         "early_stopping": early_stopping},
        "add_to_pro": _ADD_TO_PRO,
        "not_verified": "Training cells were checked against the installed arcgis.learn "
                        "signatures and compile cleanly, but have not been run on real data "
                        "in this environment.",
    })


# ---------------------------------------------------------------------------
# The single entry point
# ---------------------------------------------------------------------------
def _project_folder() -> str | None:
    """Folder of the project open in ArcGIS Pro, via the live bridge; None if unreachable.

    Asking the bridge here saves the agent a separate call just to find where to save.
    Never raises: the bridge client turns every failure into an error envelope.
    """
    try:
        from ..live import client

        reply = client.post("ping")
        path = (reply.get("data") or {}).get("project_path") if reply.get("ok") else None
        return os.path.dirname(path) if path else None
    except Exception:  # noqa: BLE001 - location lookup is best effort
        return None


_RASTER_PLACEHOLDER = "<PUT THE PATH TO THE {} IMAGE HERE>"


@guard
def create_pretrained_notebook(
    model: str,
    out_path: str,
    input_raster: str | None = None,
    second_raster: str | None = None,
    batch_size: int | None = None,
    options: dict | None = None,
    title: str | None = None,
    overwrite: bool = False,
    data_path_later: bool = False,
) -> dict:
    """Write a notebook that downloads a ready-made Esri model and runs it. No training.

    ``data_path_later=True`` leaves the two image paths as marked placeholders, but only
    because the USER said they will type them. Nothing else is invented: every other value
    is the tool's own default.
    """
    key = (model or "").strip().lower()
    if key not in PRETRAINED:
        return err(f"Unknown pretrained model '{model}'.", valid=sorted(PRETRAINED),
                   code="unknown_model")
    entry = PRETRAINED[key]

    placeholders = False
    if data_path_later:
        if not input_raster:
            input_raster, placeholders = _RASTER_PLACEHOLDER.format("BEFORE-date"), True
        if not second_raster:
            second_raster, placeholders = _RASTER_PLACEHOLDER.format("AFTER-date"), True
    missing = [k for k, v in (("input_raster", input_raster), ("second_raster", second_raster))
               if not v]
    if missing:
        qs = [q for q in _pretrained_questions(entry) if q["key"] in missing]
        return err("Missing answers: " + ", ".join(missing) + ". Ask the user — do not guess these.",
                   code="needs_answers", missing=missing, questions=qs)

    if batch_size is not None and int(batch_size) < 1:
        return err("batch_size must be at least 1.", code="bad_value")
    options = dict(options or {})
    for k, v in options.items():
        if k not in _PRETRAINED_OPTIONS:
            return err(f"Unknown option '{k}'. Valid: {sorted(_PRETRAINED_OPTIONS)}.",
                       code="unsupported_option")
        if _PRETRAINED_OPTIONS[k] and v not in _PRETRAINED_OPTIONS[k]:
            return err(f"Option '{k}' must be one of {list(_PRETRAINED_OPTIONS[k])}.",
                       code="bad_value")
        if not isinstance(v, str):
            return err(f"Option '{k}' must be a string.", code="bad_value")

    if not str(out_path).lower().endswith(".ipynb"):
        out_path = f"{out_path}.ipynb"
    lo, hi = entry["cell_size_m"]
    folder = options.get("model_folder")
    facts = "\n".join(f"- {h}" for h in entry["heads_up"])

    cells = [
        _md(f"# {title or entry['label']}\n\n"
            "Generated by Salah MCP. This is a **ready-made Esri model**: nothing is trained, "
            "so there is no batch size, epochs or learning rate here — only the two images.\n\n"
            f"{facts}\n\n"
            "Run the cells **one at a time, top to bottom**."),
        _md("## 1. Environment check"),
        _code("import sys, arcpy\n"
              "print('ArcPy', arcpy.GetInstallInfo()['Version'], '| Image Analyst:',\n"
              "      arcpy.CheckExtension('ImageAnalyst'))\n"
              "try:\n"
              "    import torch\n"
              "    print('torch', torch.__version__, '| CUDA GPU:', torch.cuda.is_available())\n"
              "except ImportError:\n"
              "    print('torch is missing - install the Deep Learning Libraries for ArcGIS Pro.')"),
        _md("## 2. Parameters"),
        _code("\n".join([
            f"BEFORE_RASTER = {_path(input_raster)}",
            f"AFTER_RASTER  = {_path(second_raster)}",
            f"MODEL_ITEM_ID = {entry['item_id']!r}   # {entry['label']} on ArcGIS Online",
            f"MODEL_FOLDER  = {_path(folder) if folder else None}   # None -> <current folder>\\esri_models",
            f"BATCH_SIZE    = {int(batch_size) if batch_size else None}   # None -> the model's own default",
            f"PROCESSOR     = {options.get('processor')!r}   # 'GPU' / 'CPU' / None = let ArcGIS decide",
            f"OUTPUT_NAME   = {options.get('output_name', 'building_change')!r}",
        ])),
        _md("## 3. About the Esri model\nRead the licence **before** downloading."),
        _code("from arcgis.gis import GIS\n"
              "gis = GIS()          # anonymous: the item is public\n"
              "item = gis.content.get(MODEL_ITEM_ID)\n"
              "size = f'{item.size / 1e6:.0f} MB' if item.size and item.size > 0 else 'size n/a'\n"
              "print(item.title, '|', item.type, '|', size)\n"
              "print('snippet :', item.snippet)\n"
              "import re\n"
              "raw = item.licenseInfo or ''   # the item stores it as HTML\n"
              "text = re.sub(r'<[^>]+>', ' ', raw).replace('&nbsp;', ' ')\n"
              "print('licence :', re.sub(r'\\s+', ' ', text).strip() or 'see the item page')\n"
              "for link in re.findall(r'https?://[^\\s<>]+', raw):\n"
              "    link = link.strip(chr(39) + chr(34))\n"
              "    if not link.lower().endswith(('.png', '.jpg', '.gif')):\n"
              "        print('          ', link)\n"
              "if 'Requires Subscription' in (item.typeKeywords or []):\n"
              "    print('\\nNOTE: this item is flagged \"Requires Subscription\" - make sure your '\n"
              "          'ArcGIS account is entitled to use it.')"),
        _md("## 4. Download the model package\nSkipped if it is already there."),
        _code("from pathlib import Path\n"
              "model_dir = Path(MODEL_FOLDER) if MODEL_FOLDER else Path.cwd() / 'esri_models'\n"
              "model_dir.mkdir(parents=True, exist_ok=True)\n"
              "dlpk_path = model_dir / f'{MODEL_ITEM_ID}.dlpk'\n"
              "if not dlpk_path.exists():\n"
              "    item.download(save_path=str(model_dir), file_name=dlpk_path.name)\n"
              "print('model package:', dlpk_path, f'({dlpk_path.stat().st_size / 1e6:.0f} MB)')"),
        _md("## 5. Check the two images\nStops with a clear message until the paths are set."),
        _code("for name, path in (('BEFORE_RASTER', BEFORE_RASTER), ('AFTER_RASTER', AFTER_RASTER)):\n"
              "    if not arcpy.Exists(path):\n"
              "        raise FileNotFoundError(f'Set {name} in the Parameters cell first (it is {path!r}).')\n"
              "before, after = arcpy.Raster(BEFORE_RASTER), arcpy.Raster(AFTER_RASTER)\n"
              "for label, r in (('before', before), ('after ', after)):\n"
              "    print(f'{label} {r.width} x {r.height} px | {r.bandCount} bands | '\n"
              "          f'cell {r.meanCellWidth:.3f} | {r.spatialReference.name}')\n"
              "    if 'meter' in (r.spatialReference.linearUnitName or '').lower() and \\\n"
              f"            not {lo} <= r.meanCellWidth <= {hi}:\n"
              f"        print(f'   WARNING: {{r.meanCellWidth:.3f}} m cells are outside the {lo}-{hi} m '\n"
              "              'this model was built for.')\n"
              "if before.spatialReference.name != after.spatialReference.name:\n"
              "    print('WARNING: different coordinate systems - align the two images first.')"),
        _md("## 6. Detect change\n**Long-running**, especially on CPU."),
        _code("arcpy.CheckOutExtension('ImageAnalyst')\n"
              "if PROCESSOR:\n"
              "    arcpy.env.processorType = PROCESSOR\n"
              "change = arcpy.ia.DetectChangeUsingDeepLearning(\n"
              "    BEFORE_RASTER, AFTER_RASTER, str(dlpk_path),\n"
              "    f'batch_size {BATCH_SIZE}' if BATCH_SIZE else '#')"),
        _md("## 7. Save the result"),
        _code("try:\n"
              "    DEFAULT_GDB = arcpy.mp.ArcGISProject('CURRENT').defaultGeodatabase\n"
              "except OSError:  # not running inside ArcGIS Pro (seen when run headless)\n"
              "    DEFAULT_GDB = arcpy.env.scratchGDB\n"
              'OUT_RASTER = f"{DEFAULT_GDB}\\\\{OUTPUT_NAME}"\n'
              "change.save(OUT_RASTER)\n"
              "print('wrote', OUT_RASTER)"),
        _md("## 8. Add it to the map"),
        _code("try:\n"
              "    arcpy.mp.ArcGISProject('CURRENT').activeMap.addDataFromPath(OUT_RASTER)\n"
              "    print('added to the active map')\n"
              "except (OSError, AttributeError):\n"
              "    print('Not inside ArcGIS Pro (or no active map) - add the raster by hand.')"),
        _md("## 9. Value range\nThe output is continuous (change magnitude), so look at its range "
            "before choosing a threshold."),
        _code("for stat in ('MINIMUM', 'MAXIMUM', 'MEAN'):\n"
              "    print(f'{stat:8}', arcpy.management.GetRasterProperties(OUT_RASTER, stat).getOutput(0))"),
    ]

    failure = _write(cells, out_path, overwrite)
    if failure:
        return failure
    return ok({
        "status": "created", "mode": "pretrained",
        "notebook": os.path.abspath(out_path),
        "model": entry["label"], "item_id": entry["item_id"], "task": entry["task"],
        "image_paths_are_placeholders": placeholders,
        "cells": len(cells), "outline": _outline(cells),
        "heads_up": entry["heads_up"],
        "add_to_pro": _ADD_TO_PRO,
        "not_verified": "The model was not downloaded or run here: no 10-20 cm before/after "
                        "pair exists on this machine. Only the first cells (environment, "
                        "item metadata) were executed.",
    })


@guard
def notebook(
    goal: str = "detect buildings",
    out_path: str | None = None,
    model: str | None = None,
    training_data: str | None = None,
    batch_size: int | None = None,
    epochs: int | None = None,
    learning_rate: float | None = None,
    chip_size: int | None = None,
    val_split_pct: float | None = None,
    options: dict | None = None,
    mixed_precision: bool = False,
    early_stopping: bool = False,
    model_name: str | None = None,
    input_raster: str | None = None,
    input_features: str | None = None,
    second_raster: str | None = None,
    data_path_later: bool = False,
    overwrite: bool = False,
) -> dict:
    """Plan-or-create in one call. Returns status ``choose``, ``ask_user`` or ``created``."""
    key = (model or "").strip().lower() or None
    if key is not None and key not in MODELS and key not in PRETRAINED:
        return err(f"Unknown model '{model}'.", valid=sorted(MODELS) + sorted(PRETRAINED),
                   code="unknown_model")

    reason, alternatives = None, []
    if key is None:
        routed = plan(goal)["data"]
        if routed["mode"] == "choose":
            return ok({"status": "choose", **{k: v for k, v in routed.items() if k != "mode"}})
        key, reason, alternatives = routed["recommended"], routed["reason"], routed["alternatives"]
    pretrained = key in PRETRAINED
    cfg = PRETRAINED[key] if pretrained else MODELS[key]

    need_folder = not out_path
    missing = []
    if pretrained:
        if not input_raster and not data_path_later:
            missing.append("input_raster")
        if not second_raster and not data_path_later:
            missing.append("second_raster")
    else:
        if not training_data and not data_path_later:
            missing.append("training_data")
        if batch_size in (None, ""):
            missing.append("batch_size")
        if epochs in (None, ""):
            missing.append("epochs")
    # While still interviewing only the cheap "is the bridge there?" probe runs. The real
    # project lookup measured 120-1300 ms — far more than writing the notebook (~5 ms) —
    # so it waits until everything else is answered and we are about to write.
    if need_folder and not _bridge_listening():
        missing.append("out_path")
    if not missing and need_folder:
        folder = _project_folder()
        if folder:
            suffix = "" if pretrained else "_training"
            out_path = os.path.join(folder, f"{_slug(cfg['label'])}{suffix}.ipynb")
        else:
            missing.append("out_path")

    if missing:
        given = {k for k, v in (
            ("training_data", training_data), ("batch_size", batch_size), ("epochs", epochs),
            ("learning_rate", learning_rate), ("chip_size", chip_size),
            ("val_split_pct", val_split_pct), ("model_name", model_name),
            ("input_raster", input_raster), ("input_features", input_features),
            ("second_raster", second_raster),
            ("mixed_precision", mixed_precision or None), ("early_stopping", early_stopping or None),
        ) if v not in (None, "")} | set(options or {})
        questions = [q for q in (_pretrained_questions(cfg) if pretrained else _questions(cfg))
                     if q["key"] not in given]
        if "out_path" in missing:
            questions.insert(0, {
                "key": "out_path", "required": True, "default": None,
                "question": "Where should the notebook be saved (a .ipynb path)?",
                "why": "The live bridge could not tell which Pro project is open, so the "
                       "default (that project's folder) is unavailable."})
        payload = {
            "status": "ask_user",
            "model": key, "label": cfg["label"], "task": cfg["task"],
            "reason": reason or f"You chose {cfg['label']}.",
            "alternatives": alternatives,
            "missing": missing,
            "questions": questions,
            "how_to_ask": _HOW_TO_ASK,
        }
        if pretrained:
            payload["mode"] = "pretrained"
            payload["heads_up"] = cfg["heads_up"]
        else:
            payload["formats"] = cfg["formats"]
        return ok(payload)

    if pretrained:
        return create_pretrained_notebook(
            key, out_path, input_raster, second_raster, batch_size, options,
            overwrite=overwrite, data_path_later=data_path_later)
    return create_training_notebook(
        key, out_path, training_data, batch_size, epochs, learning_rate, chip_size,
        val_split_pct, options, mixed_precision, early_stopping, model_name,
        input_raster, input_features, second_raster,
        overwrite=overwrite, data_path_later=data_path_later,
    )
