using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace SalahAIBridge
{
    /// <summary>
    /// Salah_MCP_PublishBtn — pick feature layers from a dark checklist and publish
    /// them straight to the signed-in ArcGIS Online / Portal as hosted feature
    /// layers, into a (new) folder — with data-derived metadata and an optional
    /// Web Map. No agent required.
    ///
    /// The Pro .NET SDK has no managed publish API, so the work runs in Pro's
    /// bundled Python (arcgispro-py3): arcpy sharing
    /// (getWebLayerSharingDraft → StageService → UploadServiceDefinition) sets the
    /// item summary/description/tags from each layer's geometry, feature count and
    /// fields; the optional web map is built with the ArcGIS API for Python
    /// (GIS("pro")). Prereqs: signed in + project saved.
    /// </summary>
    internal class PublishButton : ArcGIS.Desktop.Framework.Contracts.Button
    {
        // Last-resort fallback only; ResolveProPython() normally derives the path
        // from the running ArcGIS Pro process.
        private const string DefaultProPython =
            @"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe";

        protected override async void OnClick()
        {
            try
            {
                var ctx = await QueuedTask.Run(() =>
                {
                    var portal = ArcGISPortalManager.Current?.GetActivePortal();
                    var map = MapView.Active?.Map;
                    var layers = map?.GetLayersAsFlattenedList()
                                    .OfType<FeatureLayer>()
                                    .Select(l => l.Name)
                                    .Distinct()
                                    .ToList() ?? new List<string>();
                    return new Ctx
                    {
                        User = portal?.GetSignOnUsername(),
                        PortalUrl = portal?.PortalUri?.ToString(),
                        ProjectPath = Project.Current?.Path,
                        Layers = layers,
                    };
                });

                if (string.IsNullOrEmpty(ctx.User))
                {
                    MessageBox.Show("Sign in to ArcGIS Online / your portal first (top-right of ArcGIS Pro).",
                        "Salah MCP — Publish");
                    return;
                }
                if (ctx.Layers.Count == 0)
                {
                    MessageBox.Show("The active map has no feature layers to publish.", "Salah MCP — Publish");
                    return;
                }
                if (string.IsNullOrEmpty(ctx.ProjectPath) || !File.Exists(ctx.ProjectPath))
                {
                    MessageBox.Show("Save the project first (File ▸ Save) so the latest layers can be published.",
                        "Salah MCP — Publish");
                    return;
                }

                var choice = ShowPublishDialog(ctx);
                if (choice == null || choice.Layers.Count == 0)
                    return;

                try { await Project.Current.SaveAsync(); } catch { /* best effort */ }

                var (progWin, progLabel) = ShowProgress("Starting…");
                List<Result> results;
                try
                {
                    results = await PublishAsync(ctx.ProjectPath, choice.Folder, choice.MakeWebMap,
                        choice.Layers, msg => progLabel.Text = msg);
                }
                finally
                {
                    progWin.Close();
                }

                int ok = results.Count(r => r.Ok);
                string lines = string.Join("\n", results.Select(r =>
                    (r.Ok ? "✓ " : "✗ ") + r.Layer +
                    (r.Ok
                        ? (string.IsNullOrEmpty(r.Meta) ? "" : "   " + r.Meta)
                        : "   — " + r.Error)));

                MessageBox.Show(
                    $"Published {ok}/{results.Count} item(s) into folder \"{choice.Folder}\" on\n{ctx.PortalUrl}\n\n{lines}",
                    "Salah MCP — Publish");
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Publish error: {ex.Message}", "Salah MCP — Publish");
            }
        }

        // ---------------------------------------------------------------------
        //  Dark-themed layer checklist (matches ArcGIS Pro dark mode)
        // ---------------------------------------------------------------------
        private static Choice ShowPublishDialog(Ctx ctx)
        {
            Brush bgDark = Hex("#252526"), inputBg = Hex("#1e1e1e"), white = Brushes.White,
                  gray = Hex("#aaaaaa"), accent = Hex("#007acc"), btnBg = Hex("#3e3e42");
            var seg = new FontFamily("Segoe UI");

            var panel = new StackPanel { Background = bgDark };

            panel.Children.Add(new TextBlock
            {
                Text = $"Publish to {ctx.PortalUrl}  ({ctx.User})",
                Foreground = gray, FontFamily = seg, FontSize = 12,
                TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 10),
            });

            var folderBox = new TextBox
            {
                Text = "Salah MCP", Background = inputBg, Foreground = white, BorderBrush = btnBg,
                BorderThickness = new Thickness(1), Padding = new Thickness(6, 3, 6, 3),
                FontFamily = seg, FontSize = 13, CaretBrush = white, MinWidth = 220,
            };
            var folderRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 12) };
            folderRow.Children.Add(new TextBlock
            {
                Text = "New folder:", Foreground = white, FontFamily = seg, FontSize = 13,
                VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 8, 0),
            });
            folderRow.Children.Add(folderBox);
            panel.Children.Add(folderRow);

            var selectAll = new CheckBox
            {
                Content = "Select all", Foreground = gray, FontFamily = seg, FontSize = 12,
                Margin = new Thickness(0, 0, 0, 6),
            };
            panel.Children.Add(selectAll);

            // Each row = [checkbox: layer name] ............... [type combo].
            // The per-row combo is what makes mixed-type publishing possible.
            var rows = new List<(CheckBox cb, ComboBox combo)>();
            var list = new StackPanel();
            foreach (var name in ctx.Layers)
            {
                var cb = new CheckBox
                {
                    Content = name, Foreground = white, FontFamily = seg, FontSize = 13,
                    VerticalAlignment = VerticalAlignment.Center,
                };
                var combo = new ComboBox
                {
                    Width = 110, FontFamily = seg, FontSize = 12, Background = inputBg,
                    Foreground = white, BorderBrush = btnBg, BorderThickness = new Thickness(1),
                    VerticalAlignment = VerticalAlignment.Center,
                };
                foreach (var t in new[] { "Feature", "Tile", "Vector Tile" }) combo.Items.Add(t);
                
                combo.SelectedIndex = 0;

                var row = new DockPanel { Margin = new Thickness(2, 3, 2, 3) };
                DockPanel.SetDock(combo, Dock.Right);
                row.Children.Add(combo);
                row.Children.Add(cb);   // fills the remaining width to the left of the combo
                rows.Add((cb, combo));
                list.Children.Add(row);
            }
            selectAll.Checked += (_, _) => rows.ForEach(r => r.cb.IsChecked = true);
            selectAll.Unchecked += (_, _) => rows.ForEach(r => r.cb.IsChecked = false);

            panel.Children.Add(new Border
            {
                Background = inputBg, BorderBrush = btnBg, BorderThickness = new Thickness(1),
                Padding = new Thickness(8), MaxHeight = 240,
                Child = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = list },
            });

            // --- Layer Type: per-row combos above set the type for each layer.
            // These radios are a convenience that bulk-applies one type to all rows
            // (mix freely afterwards by changing individual row combos). ---
            var typeHeader = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 14, 0, 6) };
            typeHeader.Children.Add(new TextBlock
            {
                Text = "Layer Type — set all to:", Foreground = white, FontFamily = seg, FontSize = 13,
                FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center,
            });
            typeHeader.Children.Add(new TextBlock
            {
                Text = " ⓘ", Foreground = accent, FontFamily = seg, FontSize = 13,
                VerticalAlignment = VerticalAlignment.Center,
                ToolTip = "Each layer is published as its own type — set them individually in the list above, " +
                          "or use these to apply one type to every layer.\n\n" +
                          "Feature: editable hosted feature layer (vector).\n" +
                          "Tile: pre-rendered raster map tiles (fast, not editable).\n" +
                          "Vector Tile: vector tiles styled on the client (crisp at any zoom).",
            });
            panel.Children.Add(typeHeader);

            void SetAllType(string display) => rows.ForEach(r => r.combo.SelectedItem = display);
            RadioButton TypeRadio(string display) => new()
            {
                Content = display, GroupName = "layertype",
                Foreground = white, FontFamily = seg, FontSize = 13, Margin = new Thickness(0, 0, 14, 0),
            };
            var typeFeature = TypeRadio("Feature");
            var typeTile = TypeRadio("Tile");
            var typeVector = TypeRadio("Vector Tile");
            typeFeature.Checked += (_, _) => SetAllType("Feature");
            typeTile.Checked += (_, _) => SetAllType("Tile");
            typeVector.Checked += (_, _) => SetAllType("Vector Tile");
            var setAllRow = new StackPanel { Orientation = Orientation.Horizontal };
            setAllRow.Children.Add(typeFeature);
            setAllRow.Children.Add(typeTile);
            setAllRow.Children.Add(typeVector);
            panel.Children.Add(setAllRow);

            var makeMap = new CheckBox
            {
                Content = "Create a Web Map of these layers in the folder",
                Foreground = white, FontFamily = seg, FontSize = 13,
                Margin = new Thickness(0, 12, 0, 0),
            };
            panel.Children.Add(makeMap);

            var start = PillButton.Create("Start Publishing", 140, 30, accent, white, isDefault: true);
            start.Margin = new Thickness(0, 0, 10, 0);

            var cancel = PillButton.Create("Cancel", 90, 30, btnBg, white, isCancel: true);
            var btnRow = new StackPanel
            {
                Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(0, 16, 0, 0),
            };
            btnRow.Children.Add(start);
            btnRow.Children.Add(cancel);
            panel.Children.Add(btnRow);

            var window = new Window
            {
                Title = "Salah MCP — Publish layers",
                Content = new Border { Background = bgDark, Padding = new Thickness(18), Child = panel },
                Width = 460, SizeToContent = SizeToContent.Height, ResizeMode = ResizeMode.NoResize,
                WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = bgDark,
            };

            static string Code(string display) =>
                display == "Tile" ? "TILE" : display == "Vector Tile" ? "VECTOR_TILE" : "FEATURE";

            Choice choice = null;
            start.Click += (_, _) =>
            {
                var picked = rows.Where(r => r.cb.IsChecked == true)
                    .Select(r => new LayerSpec
                    {
                        Name = (string)r.cb.Content,
                        Type = Code((string)r.combo.SelectedItem ?? "Feature"),
                    })
                    .ToList();
                if (picked.Count == 0)
                {
                    MessageBox.Show("Check at least one layer to publish.", "Salah MCP — Publish");
                    return;
                }
                choice = new Choice
                {
                    Layers = picked,
                    Folder = string.IsNullOrWhiteSpace(folderBox.Text) ? "Salah MCP" : folderBox.Text.Trim(),
                    MakeWebMap = makeMap.IsChecked == true,
                };
                window.DialogResult = true;
            };
            window.ShowDialog();
            return choice;
        }

        // ---------------------------------------------------------------------
        //  Live "Publishing…" window (indeterminate; text updated per step)
        // ---------------------------------------------------------------------
        private static (Window window, TextBlock label) ShowProgress(string message)
        {
            var label = new TextBlock
            {
                Text = message, Foreground = Brushes.White, FontFamily = new FontFamily("Segoe UI"),
                FontSize = 13, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14),
            };
            var panel = new StackPanel();
            panel.Children.Add(label);
            panel.Children.Add(new ProgressBar
            {
                IsIndeterminate = true, Height = 10, Foreground = Hex("#007acc"),
                Background = Hex("#1e1e1e"), BorderThickness = new Thickness(0),
            });

            // Explicit "Hide" — tucks the window to the taskbar so you can go do
            // something else (browser, another tool) while publishing keeps running
            // in the background. Restore it any time from the taskbar; it closes
            // itself when the work finishes.
            var hide = PillButton.Create("Hide", 90, 28, Hex("#3e3e42"), Brushes.White);
            hide.HorizontalAlignment = HorizontalAlignment.Right;
            hide.Margin = new Thickness(0, 16, 0, 0);
            panel.Children.Add(hide);

            // Modeless + a normal title bar so you can minimize / move / resize it
            // and keep working in ArcGIS Pro while publishing runs in the background.
            // Not Topmost, and shown in the taskbar so it can be restored after hiding.
            var win = new Window
            {
                Title = "Salah MCP — Publishing (running in background)",
                Content = new Border { Background = Hex("#252526"), Padding = new Thickness(20), Child = panel },
                Width = 420, Height = 175, MinWidth = 320, MinHeight = 140,
                SizeToContent = SizeToContent.Manual, ResizeMode = ResizeMode.CanResize,
                WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = Hex("#252526"),
                WindowStyle = WindowStyle.SingleBorderWindow, ShowInTaskbar = true, Topmost = false,
            };
            hide.Click += (_, _) => win.WindowState = WindowState.Minimized;
            win.Show();
            return (win, label);
        }

        // ---------------------------------------------------------------------
        //  Direct publish via Pro's Python (arcpy sharing + ArcGIS API webmap)
        // ---------------------------------------------------------------------
        private static async Task<List<Result>> PublishAsync(
            string projectPath, string folder, bool makeWebMap,
            List<LayerSpec> layers, Action<string> onProgress)
        {
            string py = ResolveProPython();
            if (py == null)
                return new() { new Result { Layer = "*", Ok = false,
                    Error = "Could not locate arcgispro-py3 python.exe. Set ARCGIS_PRO_PYTHON to its full path." } };

            string scriptPath = Path.Combine(Path.GetTempPath(), "geogenai_publish.py");
            File.WriteAllText(scriptPath, PublishScript);

            var psi = new ProcessStartInfo
            {
                FileName = py,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.ArgumentList.Add(scriptPath);
            psi.ArgumentList.Add(projectPath);
            psi.ArgumentList.Add(folder);
            psi.ArgumentList.Add(makeWebMap ? "1" : "0");
            // Each layer is passed as a (name, type) pair so types can be mixed.
            foreach (var l in layers)
            {
                psi.ArgumentList.Add(l.Name);
                psi.ArgumentList.Add(string.IsNullOrWhiteSpace(l.Type) ? "FEATURE" : l.Type);
            }

            using var proc = new Process { StartInfo = psi };
            proc.Start();
            var errTask = proc.StandardError.ReadToEndAsync();

            string resultJson = null;
            string line;
            while ((line = await proc.StandardOutput.ReadLineAsync()) != null)
            {
                if (line.StartsWith("GEOGENAI_PROGRESS:", StringComparison.Ordinal))
                    onProgress?.Invoke(line["GEOGENAI_PROGRESS:".Length..]);
                else if (line.StartsWith("GEOGENAI_RESULT:", StringComparison.Ordinal))
                    resultJson = line["GEOGENAI_RESULT:".Length..];
            }
            string stderr = await errTask;
            await proc.WaitForExitAsync();

            if (string.IsNullOrWhiteSpace(resultJson))
                return new() { new Result { Layer = "*", Ok = false,
                    Error = "publish script failed: " + (string.IsNullOrWhiteSpace(stderr) ? "(no output)" : stderr.Trim()) } };

            var results = new List<Result>();
            using var doc = JsonDocument.Parse(resultJson.Trim());
            foreach (var el in doc.RootElement.EnumerateArray())
            {
                var r = new Result
                {
                    Layer = el.GetProperty("layer").GetString(),
                    Ok = el.GetProperty("ok").GetBoolean(),
                    Error = el.TryGetProperty("error", out var e) ? e.GetString() : null,
                };
                if (el.TryGetProperty("meta", out var m) && m.ValueKind == JsonValueKind.Object)
                    r.Meta = MetaToString(m);
                results.Add(r);
            }
            return results;
        }

        private static string MetaToString(JsonElement m)
        {
            var parts = new List<string>();
            if (m.TryGetProperty("published_as", out var pa) && pa.ValueKind == JsonValueKind.String)
                parts.Add(pa.GetString() switch { "TILE" => "Tile", "VECTOR_TILE" => "Vector Tile", _ => "Feature" });
            if (m.TryGetProperty("geometry", out var g) && g.ValueKind == JsonValueKind.String) parts.Add(g.GetString());
            if (m.TryGetProperty("features", out var f) && f.ValueKind == JsonValueKind.Number) parts.Add($"{f.GetInt32()} features");
            if (m.TryGetProperty("fields", out var fl) && fl.ValueKind == JsonValueKind.Number) parts.Add($"{fl.GetInt32()} fields");
            if (m.TryGetProperty("crs", out var c) && c.ValueKind == JsonValueKind.String) parts.Add(c.GetString());
            if (m.TryGetProperty("title", out var t) && t.ValueKind == JsonValueKind.String) parts.Add(t.GetString());
            if (m.TryGetProperty("layers", out var ly) && ly.ValueKind == JsonValueKind.Number) parts.Add($"{ly.GetInt32()} layers");
            return string.Join(" · ", parts);
        }

        private static string ResolveProPython()
        {
            string env = Environment.GetEnvironmentVariable("ARCGIS_PRO_PYTHON")
                         ?? Environment.GetEnvironmentVariable("CLI_ANYTHING_ARCGIS_PYTHON");
            if (!string.IsNullOrWhiteSpace(env) && File.Exists(env))
                return env;

            try
            {
                string exe = Process.GetCurrentProcess().MainModule?.FileName;
                if (!string.IsNullOrEmpty(exe))
                {
                    string binDir = Path.GetDirectoryName(exe);
                    string candidate = Path.Combine(binDir, "Python", "envs", "arcgispro-py3", "python.exe");
                    if (File.Exists(candidate))
                        return candidate;
                }
            }
            catch { /* MainModule can be inaccessible; fall through */ }

            return File.Exists(DefaultProPython) ? DefaultProPython : null;
        }

        private static SolidColorBrush Hex(string hex) =>
            new((Color)ColorConverter.ConvertFromString(hex));

        private sealed class Ctx
        {
            public string User, PortalUrl, ProjectPath;
            public List<string> Layers = new();
        }

        private sealed class LayerSpec
        {
            public string Name;
            public string Type = "FEATURE";   // FEATURE | TILE | VECTOR_TILE
        }

        private sealed class Choice
        {
            public List<LayerSpec> Layers = new();
            public string Folder;
            public bool MakeWebMap;
        }

        private sealed class Result
        {
            public string Layer, Error, Meta;
            public bool Ok;
        }

        // arcpy sharing + (optional) ArcGIS API web map. Emits GEOGENAI_PROGRESS:<msg>
        // lines for live status and one GEOGENAI_RESULT:<json> line at the end.
        private const string PublishScript =
"""
import arcpy, os, sys, tempfile, json, time

aprx_path = sys.argv[1]
folder = sys.argv[2]
make_webmap = sys.argv[3] == "1"
# Remaining args are (name, type) pairs, so each layer can be its own type.
_rest = sys.argv[4:]
layer_specs = []
for _i in range(0, len(_rest) - 1, 2):
    _t = _rest[_i + 1].upper()
    if _t not in ("FEATURE", "TILE", "VECTOR_TILE"):
        _t = "FEATURE"
    layer_specs.append((_rest[_i], _t))

# Smart, data-derived metadata lives in the installed package so it stays
# testable (see pro/metadata.py). Fall back to a plain description if the
# package isn't importable in this interpreter.
try:
    from arcgis_pro_salah_mcp.pro import metadata as _meta
except Exception:
    _meta = None

def _op_layer(service, title, url, stype):
    # Build the web map operational-layer entry for the published service type.
    base = {"id": service, "title": title, "visibility": True}
    u = url.rstrip("/")
    if stype == "TILE":
        base.update(layerType="ArcGISTiledMapServiceLayer", url=u)
    elif stype == "VECTOR_TILE":
        base.update(layerType="VectorTileLayer", styleUrl=u + "/resources/styles/root.json")
    else:
        base.update(layerType="ArcGISFeatureLayer", url=u + "/0")
    return base

def _describe(name, shape, all_fields, count, sr):
    if _meta is not None:
        md = _meta.build_item_metadata(name, shape, all_fields, count, sr)
        return md["summary"], md["description"], md["tags"], md["theme_label"]
    data_fields = [f for f in all_fields if f.lower() not in
                   ("objectid", "fid", "shape", "shape_length", "shape_area", "globalid")]
    flist = ", ".join(data_fields[:25]) + ("..." if len(data_fields) > 25 else "")
    summary = "%s layer - %d features - %d fields" % (shape, count, len(data_fields))
    description = ("%s feature layer with %d features and %d attribute fields (CRS: %s). "
                  "Fields: %s. Published from ArcGIS Pro by Salah MCP." %
                  (shape, count, len(data_fields), sr, flist))
    tags = ["Salah MCP", shape] + [w for w in name.replace("_", " ").split() if len(w) > 2]
    return summary, description, tags, None

def emit(msg):
    print("GEOGENAI_PROGRESS:" + msg, flush=True)

# Lazily opened (and cached) ArcGIS API connection to the signed-in Pro portal,
# shared by the vector-tile overwrite step and the web-map builder below.
_GIS = None
def _gis():
    global _GIS
    if _GIS is None:
        from arcgis.gis import GIS
        _GIS = GIS("pro")
    return _GIS

def _delete_existing(service):
    # Vector tile / scene layers can't overwrite via a draft property, so emulate
    # the requested overwrite: remove any same-named items we own before republish.
    try:
        gis = _gis()
        me = gis.users.me.username
        for it in gis.content.search('title:"%s" AND owner:%s' % (service, me), max_items=25):
            if it.title == service:
                try:
                    it.delete()
                except Exception:
                    pass
    except Exception:
        pass

results = []
published = []

try:
    aprx = arcpy.mp.ArcGISProject(aprx_path)
except Exception as e:
    print("GEOGENAI_RESULT:" + json.dumps([{"layer": "*", "ok": False, "error": "open project: " + str(e)}]), flush=True)
    sys.exit(0)

tmp = tempfile.mkdtemp()
total = len(layer_specs)
for i, (name, layer_type) in enumerate(layer_specs, 1):
    try:
        emit("[%d/%d] Reading '%s'..." % (i, total, name))
        found = None
        for m in aprx.listMaps():
            for l in m.listLayers():
                if l.name == name and getattr(l, "isFeatureLayer", False):
                    found = (m, l)
                    break
            if found:
                break
        if found is None:
            results.append({"layer": name, "ok": False, "error": "feature layer not found"})
            continue
        the_map, lyr = found

        # --- smart, data-derived metadata (detects what the layer is) ---
        meta = {}
        summary = "Published by Salah MCP"
        description = "Published from ArcGIS Pro by Salah MCP."
        tags = ["Salah MCP"]
        theme_label = None
        try:
            src = lyr.dataSource
            desc = arcpy.Describe(src)
            shape = getattr(desc, "shapeType", "Feature")
            sr = getattr(getattr(desc, "spatialReference", None), "name", "Unknown")
            count = int(arcpy.management.GetCount(src)[0])
            all_fields = [f.name for f in arcpy.ListFields(src)]
            data_fields = [f.name for f in arcpy.ListFields(src) if f.type not in ("Geometry", "OID")]
            summary, description, tags, theme_label = _describe(name, shape, all_fields, count, sr)
            meta = {"published_as": layer_type, "geometry": shape, "features": count,
                    "fields": len(data_fields), "crs": sr, "theme": theme_label}
        except Exception as me:
            meta = {"error": str(me)}

        service = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
        emit("[%d/%d] Staging '%s' as %s..." % (i, total, name, layer_type))
        try:
            draft = the_map.getWebLayerSharingDraft("HOSTING_SERVER", layer_type, service, [lyr])
        except TypeError:
            # Some service types build from the whole map (no per-layer list).
            draft = the_map.getWebLayerSharingDraft("HOSTING_SERVER", layer_type, service)
        for attr, val in (("portalFolder", folder),
                          ("summary", summary), ("tags", ", ".join(dict.fromkeys(tags))),
                          ("description", description)):
            try:
                setattr(draft, attr, val)
            except Exception:
                pass
        # overwriteExistingService is valid ONLY for Feature drafts. The attribute
        # is present on the other draft types too (so hasattr can't distinguish
        # them), but Publish rejects it for Tile/Vector Tile/Scene with
        # "check input parameters: 'overwriteExistingService'". Gate strictly by
        # type; the non-Feature types are overwritten via _delete_existing instead.
        if layer_type == "FEATURE":
            try:
                draft.overwriteExistingService = True
            except Exception:
                pass
        # Feature and (raster) Tile drafts export a service definition, then
        # stage + upload it. Vector tile drafts (_VectorTileSharingDraft) have no
        # exportToSDDraft — they publish in a single step via arcpy.sharing.Publish,
        # which builds the vector tile layer (and its associated feature layer) and
        # honours the portalFolder set above.
        if hasattr(draft, "exportToSDDraft"):
            sddraft = os.path.join(tmp, service + ".sddraft")
            sd = os.path.join(tmp, service + ".sd")
            draft.exportToSDDraft(sddraft)
            arcpy.server.StageService(sddraft, sd)
            emit("[%d/%d] Uploading '%s' to ArcGIS Online..." % (i, total, name))
            arcpy.server.UploadServiceDefinition(sd, "My Hosted Services")
        else:
            emit("[%d/%d] Publishing '%s' as %s..." % (i, total, name, layer_type))
            import arcpy.sharing
            # No overwrite flag for these drafts — clear any prior same-named
            # items first so a re-publish doesn't collide with itself.
            _delete_existing(service)
            arcpy.sharing.Publish(draft)
        published.append({"name": name, "service": service, "theme": theme_label, "type": layer_type})
        results.append({"layer": name, "ok": True, "meta": meta})
    except Exception as e:
        results.append({"layer": name, "ok": False, "error": str(e)})

if make_webmap and published:
    try:
        emit("Creating web map...")
        gis = _gis()
        me = gis.users.me.username
        try:
            gis.content.create_folder(folder)
        except Exception:
            pass
        op_layers = []
        for p in published:
            item = None
            for _ in range(6):
                hits = gis.content.search('title:"%s" AND owner:%s' % (p["service"], me), max_items=10)
                item = next((it for it in hits if it.title == p["service"]), None)
                if item:
                    break
                time.sleep(2)
            if item and item.url:
                op_layers.append(_op_layer(p["service"], p["name"], item.url, p["type"]))
        if op_layers:
            webmap_json = {
                "operationalLayers": op_layers,
                "baseMap": {"title": "Topographic", "baseMapLayers": [
                    {"id": "basemap", "layerType": "ArcGISTiledMapServiceLayer",
                     "url": "https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer"}]},
                "spatialReference": {"wkid": 102100, "latestWkid": 3857},
                "version": "2.27"
            }
            layer_infos = [{"name": p["name"], "theme_label": p.get("theme")} for p in published]
            wm_title = folder + " Web Map"
            if _meta is not None:
                wm_desc = _meta.build_webmap_description(layer_infos, wm_title)
            else:
                wm_desc = ("Web map of " + ", ".join(p["name"] for p in published) +
                           ". Created from ArcGIS Pro by Salah MCP.")
            snippet = ("Web map of " + ", ".join(p["name"] for p in published))[:250]
            props = {"title": wm_title, "type": "Web Map",
                     "text": json.dumps(webmap_json), "tags": "Salah MCP",
                     "snippet": snippet, "description": wm_desc}
            gis.content.add(props, folder=folder)
            results.append({"layer": "(web map)", "ok": True,
                            "meta": {"title": props["title"], "layers": len(op_layers)}})
        else:
            results.append({"layer": "(web map)", "ok": False, "error": "no published items found yet"})
    except Exception as e:
        results.append({"layer": "(web map)", "ok": False, "error": "web map: " + str(e)})

print("GEOGENAI_RESULT:" + json.dumps(results), flush=True)
""";
    }
}
