bl_info = {
    "name": "UIM Asset Batch I/O (.json + .txt)",
    "author": "A Mudkip",
    "version": (1, 3, 2), 
    "blender": (3, 0, 0),
    "location": "File > Import‑Export > UIM Asset Batch (.json)",
    "description": "Import and export UIM JSON meshes in batches and automatically drop a matching .txt file in legacy PlgDatas format.",
    "category": "Import‑Export",
}

import os
import json
import re

import bpy
import bmesh
from bpy.types import Operator
from bpy.props import (
    StringProperty,
    BoolProperty,
    CollectionProperty,
)
from bpy_extras.io_utils import ImportHelper, ExportHelper


def _parse_uim_json(filepath, invert_y=False):
    with open(filepath, "r", encoding="utf8") as fp:
        data = json.load(fp)
    if not isinstance(data, list) or not data:
        raise ValueError("Invalid UIM format – root must be a non‑empty list")

    uim = data[0]["Properties"]["UimData"]
    verts2d = uim["p2DGeomVertex"]
    indices = uim["Indices"]

    sign = -1.0 if invert_y else 1.0
    verts = [(v["x"], v["y"] * sign, 0.0) for v in verts2d]
    faces = [(indices[i], indices[i + 1], indices[i + 2]) for i in range(0, len(indices), 3)]
    return verts, faces


def _create_mesh(context, name, verts, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate(verbose=False)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    return obj

def _write_txt(filepath, verts, indices, name):

    vcount = len(verts)
    icount = len(indices)
    pcount = icount // 3

    xs = [v["x"] for v in verts]
    ys = [v["y"] for v in verts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    verts_str = ",".join(f"(X={v['x']:.6f},Y={v['y']:.6f})" for v in verts)

    txt = (
        f"(frameSkip=0,frameNum=1,vertexNum={vcount},polygonNum={pcount},indexNum={icount},coordinate=3,"\
        f"geomFormat=1,animFormat=1,p2DGeomVertex=(({verts_str})))"
    )

    # txt += f",MinX={min_x:.6f},MinY={min_y:.6f},MaxX={max_x:.6f},MaxY={max_y:.6f})"

    with open(filepath, "w", encoding="utf8") as fp:
        fp.write(txt)


def _add_visibility_keyframes(obj, frame_idx):
    scene = bpy.context.scene
    if scene.frame_end < frame_idx + 1:
        scene.frame_end = frame_idx + 1

    for f in (0, frame_idx - 1):
        if f < 0:
            continue
        scene.frame_set(f)
        obj.hide_viewport = True
        obj.hide_render = True
        obj.keyframe_insert(data_path="hide_viewport")
        obj.keyframe_insert(data_path="hide_render")

    scene.frame_set(frame_idx)
    obj.hide_viewport = False
    obj.hide_render = False
    obj.keyframe_insert(data_path="hide_viewport")
    obj.keyframe_insert(data_path="hide_render")

    scene.frame_set(frame_idx + 1)
    obj.hide_viewport = True
    obj.hide_render = True
    obj.keyframe_insert(data_path="hide_viewport")
    obj.keyframe_insert(data_path="hide_render")

class IMPORT_OT_uim_batch(Operator, ImportHelper):
    bl_idname = "import_scene.uim_json_batch"
    bl_label = "Import UIM Asset Batch (.json)"
    bl_options = {"PRESET", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    files: CollectionProperty(type=bpy.types.OperatorFileListElement)

    invert_y: BoolProperty(
        name="Invert Y axis",
        description="Flip Y‑coordinates (screen→Blender)",
        default=True,
    )

    build_animation: BoolProperty(
        name="Generate visibility flipbook",
        description="Turn each mesh into a one‑frame visibility key so the list acts like a flip‑book animation.",
        default=False,
    )

    def execute(self, context):
        directory = os.path.dirname(self.filepath)
        selected = [f.name for f in self.files] if self.files else [os.path.basename(self.filepath)]

        def _natural_key(s):
            return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
        selected.sort(key=_natural_key)

        imported = []
        for fname in selected:
            fpath = os.path.join(directory, fname)
            try:
                verts, faces = _parse_uim_json(fpath, self.invert_y)
            except Exception as exc:
                self.report({"WARNING"}, f"Skipping {fname}: {exc}")
                continue
            obj = _create_mesh(context, os.path.splitext(fname)[0], verts, faces)
            imported.append((obj, fname))

        if self.build_animation:
            frame_regex = re.compile(r"(\d+)(?=\.json$)")
            for obj, fname in imported:
                m = frame_regex.search(fname)
                frame_idx = int(m.group(1)) if m else imported.index((obj, fname))
                _add_visibility_keyframes(obj, frame_idx)

        return {'FINISHED'}

class EXPORT_OT_uim_batch(Operator, ExportHelper):
    bl_idname = "export_scene.uim_json_batch"
    bl_label = "Export UIM Asset Batch (.json + .txt)"
    bl_options = {"PRESET", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    invert_y: BoolProperty(
        name="Invert Y axis",
        description="Flip Y‑coordinates (Blender→screen)",
        default=True,
    )

    selected_only: BoolProperty(
        name="Selected objects only",
        default=True,
    )

    def execute(self, context):
        directory = os.path.dirname(self.filepath) or bpy.path.abspath("//")
        sign = -1.0 if self.invert_y else 1.0

        objects = context.selected_objects if self.selected_only else context.scene.objects
        exported = 0
        for obj in objects:
            if obj.type != 'MESH':
                continue

            mesh = obj.to_mesh()
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            bm.to_mesh(mesh)
            bm.free()

            verts = [{'x': v.co.x, 'y': v.co.y * sign} for v in mesh.vertices]
            indices = []
            for p in mesh.polygons:
                if len(p.vertices) == 3:
                    indices.extend(p.vertices)

            uim = {
                "frameNum": 1,
                "vertexNum": len(verts),
                "polygonNum": len(indices) // 3,
                "indexNum": len(indices),
                "coordinate": 3,
                "geomFormat": 1,
                "animFormat": 1,
                "p2DGeomVertex": verts,
                "p2DAnimVertex": verts, 
                "Indices": indices,
            }

            asset = {
                "Type": "UimAsset",
                "Name": obj.name,
                "Class": "UScriptClass'UimAsset'",
                "Flags": "RF_Public | RF_Standalone | RF_LoadCompleted",
                "Properties": {"UimData": uim},
            }

            json_path = os.path.join(directory, f"{obj.name}.json")
            with open(json_path, 'w', encoding='utf8') as fp:
                json.dump([asset], fp, indent=2)

            txt_path = os.path.join(directory, f"{obj.name}.txt")
            _write_txt(txt_path, verts, indices, obj.name)

            exported += 1

        self.report({'INFO'}, f"Exported {exported} UIM JSON/TXT pairs to {directory}")
        return {'FINISHED'}

classes = (
    IMPORT_OT_uim_batch,
    EXPORT_OT_uim_batch,
)

def _menu_import(self, context):
    self.layout.operator(IMPORT_OT_uim_batch.bl_idname, text="UIM Asset Batch (.json)")

def _menu_export(self, context):
    self.layout.operator(EXPORT_OT_uim_batch.bl_idname, text="UIM Asset Batch (.json + .txt)")


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister():
    from bpy.utils import unregister_class
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in classes:
        unregister_class(cls)

if __name__ == "__main__":
    register()
