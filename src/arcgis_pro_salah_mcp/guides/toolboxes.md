# Workflow -> geoprocessing tool

**Use `pro_create_python_toolbox` (.pyt).** It handles any tool, multi-input tools (Intersect, Union, Merge),
any intermediate, and is registered in the open Pro project. Verified end to end: Clip -> Intersect -> Export on
synthetic data, output equal to an independent arcpy computation, in-memory intermediates cleaned up.

- steps take `kwargs` (preferred) or `args`; `{{param}}` = a declared parameter, `{{memory}}` = an in-memory
  intermediate deleted in a `finally`, `{{stepN}}` = step N's output path.
- The FIRST load of a .pyt shows Pro's "trust this code" dialog; every bridge call waits until the user clicks
  Trust (the tool returns `pending` with that hint).
- `pro_toolbox_info what=datatypes` lists valid parameter datatypes / directions.

`pro_create_model` (.atbx, editable in ModelBuilder) is verified ONLY for linear chains of feature-class tools.
Multi-input tools and folder/file/layer intermediates are refused (`unsupported_model`) because those models
fail Pro's up-front validation; an Export -> Train -> Detect (Image Analyst) chain does not validate yet.
`pro_toolbox_info what=model atbx_path=...` reads any .atbx back, Esri's own included.
