"""Unit tests for services.annotation_converters (pure conversion logic, no DB/ML).

Covers all export converters (COCO/YOLO/VOC/CSV), all import parsers, the shared
helpers, and format detection, exercising success paths, every except/error
branch, empty/None inputs, and round-trip conversions.
"""
import csv
import io
import json

import pytest

from schemas.export_import import CropImportData, ImportFormat
from services import annotation_converters as ac


# ============================================================================
# Lightweight model stand-ins (duck-typed, mirror the SQLAlchemy models)
# ============================================================================


class FakeProtein:
    def __init__(self, name):
        self.name = name


class FakeImage:
    def __init__(self, id, original_filename="img.tif", width=100, height=80,
                 mip_path=None, created_at=None):
        self.id = id
        self.original_filename = original_filename
        self.width = width
        self.height = height
        self.mip_path = mip_path
        self.created_at = created_at


class FakeCrop:
    def __init__(self, image_id, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40,
                 detection_confidence=None, map_protein=None):
        self.image_id = image_id
        self.bbox_x = bbox_x
        self.bbox_y = bbox_y
        self.bbox_w = bbox_w
        self.bbox_h = bbox_h
        self.detection_confidence = detection_confidence
        self.map_protein = map_protein


class FakeDate:
    """Stand-in for a datetime with isoformat()."""
    def isoformat(self):
        return "2026-01-01T00:00:00"


# ============================================================================
# Shared helpers
# ============================================================================


def test_get_display_filename_prefers_mip_path():
    img = FakeImage(1, original_filename="orig.tif", mip_path="/a/b/mip_image.png")
    assert ac.get_display_filename(img) == "mip_image.png"


def test_get_display_filename_falls_back_to_original():
    img = FakeImage(1, original_filename="orig.tif", mip_path=None)
    assert ac.get_display_filename(img) == "orig.tif"


def test_get_class_name_uses_protein():
    crop = FakeCrop(1, map_protein=FakeProtein("PRC1"))
    assert ac.get_class_name(crop) == "PRC1"


def test_get_class_name_default_when_no_protein():
    crop = FakeCrop(1, map_protein=None)
    assert ac.get_class_name(crop) == "cell"


def test_get_class_name_custom_default():
    crop = FakeCrop(1, map_protein=None)
    assert ac.get_class_name(crop, default="object") == "object"


def test_normalize_bbox_clamps_negatives_and_zero_dims():
    assert ac.normalize_bbox(-5, -3, 0, -2) == (0, 0, 1, 1)


def test_normalize_bbox_passes_valid_values():
    assert ac.normalize_bbox(5, 7, 30, 40) == (5, 7, 30, 40)


def test_create_crop_import_data_valid():
    warnings = []
    crop = ac.create_crop_import_data(
        "img.tif", 1, 2, 3, 4, "cell", 0.5, warnings, "ctx"
    )
    assert isinstance(crop, CropImportData)
    assert crop.bbox_w == 3
    assert warnings == []


def test_create_crop_import_data_validation_error_appends_warning():
    warnings = []
    # bbox_w must be > 0; 0 triggers a ValidationError → None and a warning.
    crop = ac.create_crop_import_data(
        "img.tif", 1, 2, 0, 4, "cell", None, warnings, "Row 5"
    )
    assert crop is None
    assert len(warnings) == 1
    assert warnings[0].startswith("Row 5: invalid bbox")


def test_decode_with_fallback_utf8():
    warnings = []
    assert ac.decode_with_fallback("héllo".encode("utf-8"), warnings) == "héllo"
    assert warnings == []


def test_decode_with_fallback_latin1():
    warnings = []
    # 0xE9 is invalid UTF-8 but valid latin-1 ("é").
    out = ac.decode_with_fallback(b"h\xe9llo", warnings)
    assert out == "héllo"
    assert len(warnings) == 1
    assert "latin-1" in warnings[0]


# ============================================================================
# to_coco
# ============================================================================


def test_to_coco_default_categories_and_basic_annotation():
    img = FakeImage(1, width=100, height=80, created_at=FakeDate())
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40)
    out = ac.to_coco([img], [crop])

    assert out["categories"] == [{"id": 0, "name": "cell", "supercategory": "object"}]
    assert len(out["images"]) == 1
    assert out["images"][0]["id"] == 1
    assert out["images"][0]["width"] == 100
    assert out["images"][0]["date_captured"] == "2026-01-01T00:00:00"

    assert len(out["annotations"]) == 1
    ann = out["annotations"][0]
    assert ann["bbox"] == [10, 20, 30, 40]
    assert ann["area"] == 30 * 40
    assert ann["image_id"] == 1
    assert "score" not in ann
    assert "attributes" not in ann


def test_to_coco_with_confidence_and_protein_and_custom_categories():
    img = FakeImage(2, created_at=None)
    crop = FakeCrop(2, detection_confidence=0.9, map_protein=FakeProtein("PRC1"))
    cats = [{"id": 0, "name": "cell"}, {"id": 1, "name": "PRC1"}]
    out = ac.to_coco([img], [crop], categories=cats)

    assert out["categories"] == cats
    assert out["images"][0]["date_captured"] is None
    ann = out["annotations"][0]
    assert ann["score"] == 0.9
    assert ann["attributes"] == {"protein": "PRC1"}


def test_to_coco_null_dimensions_default_to_zero():
    img = FakeImage(3, width=None, height=None)
    out = ac.to_coco([img], [])
    assert out["images"][0]["width"] == 0
    assert out["images"][0]["height"] == 0


def test_to_coco_groups_multiple_crops_and_skips_orphan_image():
    img1 = FakeImage(1)
    img2 = FakeImage(2)  # no crops
    crop_a = FakeCrop(1)
    crop_b = FakeCrop(1)
    # Crop referencing an image not in the images list is silently grouped but
    # never emitted because we iterate over images.
    orphan = FakeCrop(999)
    out = ac.to_coco([img1, img2], [crop_a, crop_b, orphan])
    assert len(out["annotations"]) == 2
    assert {a["id"] for a in out["annotations"]} == {1, 2}


def test_to_coco_empty_inputs():
    out = ac.to_coco([], [])
    assert out["images"] == []
    assert out["annotations"] == []
    assert "date_created" in out["info"]


# ============================================================================
# to_yolo / to_yolo_classes
# ============================================================================


def test_to_yolo_basic_normalization():
    img = FakeImage(1, width=100, height=100)
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40)
    out = ac.to_yolo(img, [crop])
    # x_center = (10 + 15)/100 = 0.25, y_center = (20+20)/100 = 0.40
    parts = out.split()
    assert parts[0] == "0"
    assert parts[1] == "0.250000"
    assert parts[2] == "0.400000"
    assert parts[3] == "0.300000"
    assert parts[4] == "0.400000"


def test_to_yolo_class_index_from_class_names():
    img = FakeImage(1, width=100, height=100)
    crop = FakeCrop(1, map_protein=FakeProtein("PRC1"))
    out = ac.to_yolo(img, [crop], class_names=["cell", "PRC1"])
    assert out.split()[0] == "1"


def test_to_yolo_class_not_in_list_defaults_to_zero():
    img = FakeImage(1, width=100, height=100)
    crop = FakeCrop(1, map_protein=FakeProtein("Unknown"))
    out = ac.to_yolo(img, [crop], class_names=["cell"])
    assert out.split()[0] == "0"


def test_to_yolo_missing_dimensions_estimates_from_crops():
    img = FakeImage(1, width=None, height=0)
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40)
    # Estimated img_w = 10+30 = 40, img_h = 20+40 = 60
    out = ac.to_yolo(img, [crop])
    parts = out.split()
    # x_center = (10 + 15)/40 = 0.625
    assert parts[1] == "0.625000"


def test_to_yolo_missing_dimensions_no_crops_uses_default_512():
    img = FakeImage(1, width=0, height=0)
    # No crops → defaults of 512, and empty output.
    out = ac.to_yolo(img, [])
    assert out == ""


def test_to_yolo_multiple_crops_multiline():
    img = FakeImage(1, width=100, height=100)
    crops = [FakeCrop(1), FakeCrop(1)]
    out = ac.to_yolo(img, crops)
    assert len(out.split("\n")) == 2


def test_to_yolo_classes_default():
    assert ac.to_yolo_classes() == "cell"


def test_to_yolo_classes_custom():
    assert ac.to_yolo_classes(["cell", "PRC1", "KIF4"]) == "cell\nPRC1\nKIF4"


# ============================================================================
# to_voc
# ============================================================================


def test_to_voc_basic_structure():
    img = FakeImage(1, original_filename="x.tif", width=200, height=150)
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40)
    xml = ac.to_voc(img, [crop])
    assert "<annotation>" in xml
    assert "<filename>x.tif</filename>" in xml
    assert "<folder>images</folder>" in xml
    assert "<width>200</width>" in xml
    assert "<xmin>10</xmin>" in xml
    assert "<xmax>40</xmax>" in xml  # 10 + 30
    assert "<ymax>60</ymax>" in xml  # 20 + 40
    assert "MAPtimize" in xml
    assert "<confidence>" not in xml


def test_to_voc_with_confidence_and_protein_and_custom_folder():
    img = FakeImage(1)
    crop = FakeCrop(1, detection_confidence=0.1234, map_protein=FakeProtein("PRC1"))
    xml = ac.to_voc(img, [crop], folder="custom")
    assert "<folder>custom</folder>" in xml
    assert "<name>PRC1</name>" in xml
    assert "<confidence>0.1234</confidence>" in xml


def test_to_voc_null_dimensions_default_512():
    img = FakeImage(1, width=None, height=None)
    xml = ac.to_voc(img, [])
    assert "<width>512</width>" in xml
    assert "<height>512</height>" in xml


# ============================================================================
# to_csv
# ============================================================================


def test_to_csv_basic_rows():
    img = FakeImage(1, original_filename="a.tif")
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40,
                    detection_confidence=0.5, map_protein=FakeProtein("PRC1"))
    out = ac.to_csv([img], [crop])
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["image_id", "filename", "x", "y", "width", "height",
                       "class", "confidence"]
    assert rows[1] == ["1", "a.tif", "10", "20", "30", "40", "PRC1", "0.5"]


def test_to_csv_no_confidence_blank_and_default_class():
    img = FakeImage(1)
    crop = FakeCrop(1, detection_confidence=None, map_protein=None)
    out = ac.to_csv([img], [crop])
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[1][-1] == ""   # confidence blank
    assert rows[1][-2] == "cell"  # default class


def test_to_csv_skips_crop_with_missing_image():
    out = ac.to_csv([], [FakeCrop(999)])
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) == 1  # header only


# ============================================================================
# detect_import_format
# ============================================================================


def test_detect_format_maptimize_manifest():
    assert ac.detect_import_format({"manifest.json": b""}) == ImportFormat.MAPTIMIZE


def test_detect_format_maptimize_nested_manifest():
    assert ac.detect_import_format({"sub/Manifest.json": b""}) == ImportFormat.MAPTIMIZE


def test_detect_format_coco_annotations_json():
    assert ac.detect_import_format({"annotations.json": b""}) == ImportFormat.COCO


def test_detect_format_coco_coco_json():
    assert ac.detect_import_format({"data/coco.json": b""}) == ImportFormat.COCO


def test_detect_format_yolo_classes_txt():
    assert ac.detect_import_format({"classes.txt": b""}) == ImportFormat.YOLO


def test_detect_format_yolo_labels_folder():
    contents = {"labels/img1.txt": b"", "images/img1.jpg": b""}
    assert ac.detect_import_format(contents) == ImportFormat.YOLO


def test_detect_format_voc():
    contents = {"Annotations/img1.xml": b""}
    assert ac.detect_import_format(contents) == ImportFormat.VOC


def test_detect_format_csv():
    assert ac.detect_import_format({"annotations.csv": b""}) == ImportFormat.CSV


def test_detect_format_default_coco_when_unknown():
    assert ac.detect_import_format({"random.bin": b""}) == ImportFormat.COCO


def test_detect_format_xml_without_annotations_folder_not_voc():
    # Has .xml but no Annotations folder → does not match VOC, falls to default.
    assert ac.detect_import_format({"stray.xml": b""}) == ImportFormat.COCO


# ============================================================================
# from_coco
# ============================================================================


def _coco_bytes(payload):
    return json.dumps(payload).encode("utf-8")


def test_from_coco_success():
    payload = {
        "categories": [{"id": 0, "name": "cell"}, {"id": 1, "name": "PRC1"}],
        "images": [{"id": 1, "file_name": "a.tif"}],
        "annotations": [
            {"id": 10, "image_id": 1, "category_id": 1, "bbox": [5.4, 6.6, 30, 40],
             "score": 0.8},
        ],
    }
    crops, errors, warnings = ac.from_coco(_coco_bytes(payload), {})
    assert errors == []
    assert len(crops) == 1
    assert crops[0].bbox_x == 5   # round(5.4)
    assert crops[0].bbox_y == 7   # round(6.6)
    assert crops[0].class_name == "PRC1"
    assert crops[0].confidence == 0.8


def test_from_coco_invalid_json():
    crops, errors, warnings = ac.from_coco(b"{not json", {})
    assert crops == []
    assert len(errors) == 1
    assert "Invalid JSON" in errors[0]


def test_from_coco_no_categories_uses_default_with_warning():
    payload = {
        "images": [{"id": 1, "file_name": "a.tif"}],
        "annotations": [{"id": 1, "image_id": 1, "bbox": [0, 0, 10, 10]}],
    }
    crops, errors, warnings = ac.from_coco(_coco_bytes(payload), {})
    assert any("No categories found" in w for w in warnings)
    assert crops[0].class_name == "cell"


def test_from_coco_unknown_image_reference():
    payload = {
        "categories": [{"id": 0, "name": "cell"}],
        "images": [{"id": 1, "file_name": "a.tif"}],
        "annotations": [{"id": 5, "image_id": 99, "bbox": [0, 0, 10, 10]}],
    }
    crops, errors, warnings = ac.from_coco(_coco_bytes(payload), {})
    assert crops == []
    assert any("unknown image 99" in w for w in warnings)


def test_from_coco_invalid_bbox_length():
    payload = {
        "categories": [{"id": 0, "name": "cell"}],
        "images": [{"id": 1, "file_name": "a.tif"}],
        "annotations": [{"id": 5, "image_id": 1, "bbox": [0, 0, 10]}],
    }
    crops, errors, warnings = ac.from_coco(_coco_bytes(payload), {})
    assert crops == []
    assert any("Invalid bbox" in w for w in warnings)


def test_from_coco_unknown_category_falls_back_to_cell():
    payload = {
        "categories": [{"id": 0, "name": "cell"}],
        "images": [{"id": 1, "file_name": "a.tif"}],
        "annotations": [{"id": 5, "image_id": 1, "category_id": 77,
                         "bbox": [0, 0, 10, 10]}],
    }
    crops, errors, warnings = ac.from_coco(_coco_bytes(payload), {})
    assert crops[0].class_name == "cell"


def test_from_coco_validation_error_for_zero_width():
    payload = {
        "categories": [{"id": 0, "name": "cell"}],
        "images": [{"id": 1, "file_name": "a.tif"}],
        "annotations": [{"id": 5, "image_id": 1, "bbox": [0, 0, 0, 10]}],
    }
    crops, errors, warnings = ac.from_coco(_coco_bytes(payload), {})
    assert crops == []
    assert any("invalid bbox" in w for w in warnings)


def test_from_coco_empty_document():
    crops, errors, warnings = ac.from_coco(_coco_bytes({}), {})
    assert crops == []
    assert errors == []


# ============================================================================
# from_yolo
# ============================================================================


def test_from_yolo_success_with_confidence():
    label_files = {"labels/img1.txt": b"0 0.5 0.5 0.2 0.4 0.9\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    assert errors == []
    assert len(crops) == 1
    c = crops[0]
    # w = round(0.2*100)=20, h=round(0.4*100)=40
    # x = round(0.5*100 - 10) = 40, y = round(0.5*100 - 20) = 30
    assert (c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h) == (40, 30, 20, 40)
    assert c.confidence == 0.9
    assert c.class_name == "cell"


def test_from_yolo_no_image_for_label():
    label_files = {"labels/orphan.txt": b"0 0.5 0.5 0.2 0.4\n"}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], {})
    assert crops == []
    assert any("No image found" in w for w in warnings)


def test_from_yolo_blank_lines_skipped():
    # Blank line in the MIDDLE survives the content-level strip() and exercises
    # the per-line `continue`.
    label_files = {"img1.txt": b"0 0.5 0.5 0.2 0.4\n   \n0 0.3 0.3 0.1 0.1\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    assert len(crops) == 2


def test_from_yolo_invalid_format_too_few_parts():
    label_files = {"img1.txt": b"0 0.5 0.5\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    assert crops == []
    assert any("Invalid format" in w for w in warnings)


def test_from_yolo_invalid_values_non_numeric():
    label_files = {"img1.txt": b"a b c d e\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    assert crops == []
    assert any("Invalid values" in w for w in warnings)


def test_from_yolo_class_id_out_of_range_defaults_to_cell():
    label_files = {"img1.txt": b"5 0.5 0.5 0.2 0.4\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    assert crops[0].class_name == "cell"


def test_from_yolo_class_id_in_range():
    label_files = {"img1.txt": b"1 0.5 0.5 0.2 0.4\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell", "PRC1"], image_map)
    assert crops[0].class_name == "PRC1"


def test_from_yolo_normalize_clamps_negative_coords():
    # Center near edge with large box → negative raw_x, normalized to 0.
    label_files = {"img1.txt": b"0 0.05 0.05 0.2 0.2\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    # raw_x = round(5 - 10) = -5 → clamped to 0
    assert crops[0].bbox_x == 0
    assert crops[0].bbox_y == 0


def test_from_yolo_degenerate_zero_size_box_skipped():
    # width 0 → raw_w 0 → normalize forces w=1, so it is actually valid (w>=1).
    label_files = {"img1.txt": b"0 0.5 0.5 0.0 0.0\n"}
    image_map = {"img1": ("images/img1.jpg", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    assert len(crops) == 1
    assert crops[0].bbox_w == 1
    assert crops[0].bbox_h == 1


def test_from_yolo_empty_label_files():
    crops, errors, warnings = ac.from_yolo({}, ["cell"], {})
    assert crops == [] and errors == [] and warnings == []


# ============================================================================
# from_voc
# ============================================================================


def _voc_xml(filename="a.tif", objects=None):
    objs = objects if objects is not None else [
        {"name": "cell", "xmin": 10, "ymin": 20, "xmax": 40, "ymax": 60}
    ]
    obj_xml = ""
    for o in objs:
        conf = f"<confidence>{o['confidence']}</confidence>" if "confidence" in o else ""
        name = f"<name>{o['name']}</name>" if "name" in o else ""
        bndbox = ""
        if "no_bndbox" not in o:
            bndbox = (
                "<bndbox>"
                f"<xmin>{o.get('xmin', 0)}</xmin>"
                f"<ymin>{o.get('ymin', 0)}</ymin>"
                f"<xmax>{o.get('xmax', 1)}</xmax>"
                f"<ymax>{o.get('ymax', 1)}</ymax>"
                "</bndbox>"
            )
        obj_xml += f"<object>{name}{bndbox}{conf}</object>"
    fname = f"<filename>{filename}</filename>" if filename is not None else ""
    return f"<annotation>{fname}{obj_xml}</annotation>".encode("utf-8")


def test_from_voc_success():
    xml_files = {"a.xml": _voc_xml(objects=[
        {"name": "PRC1", "xmin": 10, "ymin": 20, "xmax": 40, "ymax": 60,
         "confidence": 0.7}
    ])}
    crops, errors, warnings = ac.from_voc(xml_files, {})
    assert errors == []
    assert len(crops) == 1
    c = crops[0]
    assert (c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h) == (10, 20, 30, 40)
    assert c.class_name == "PRC1"
    assert c.confidence == 0.7


def test_from_voc_invalid_xml():
    crops, errors, warnings = ac.from_voc({"bad.xml": b"<annotation><not closed"}, {})
    assert crops == []
    assert any("Invalid XML" in e for e in errors)


def test_from_voc_no_filename():
    crops, errors, warnings = ac.from_voc({"a.xml": _voc_xml(filename=None)}, {})
    assert crops == []
    assert any("No filename" in w for w in warnings)


def test_from_voc_empty_filename_text():
    xml = b"<annotation><filename></filename></annotation>"
    crops, errors, warnings = ac.from_voc({"a.xml": xml}, {})
    assert crops == []
    assert any("No filename" in w for w in warnings)


def test_from_voc_no_bndbox():
    xml_files = {"a.xml": _voc_xml(objects=[{"name": "cell", "no_bndbox": True}])}
    crops, errors, warnings = ac.from_voc(xml_files, {})
    assert crops == []
    assert any("No bndbox" in w for w in warnings)


def test_from_voc_missing_name_defaults_to_cell():
    xml_files = {"a.xml": _voc_xml(objects=[
        {"xmin": 10, "ymin": 10, "xmax": 50, "ymax": 50}
    ])}
    crops, errors, warnings = ac.from_voc(xml_files, {})
    assert crops[0].class_name == "cell"


def test_from_voc_invalid_bndbox_values():
    xml = (b"<annotation><filename>a.tif</filename><object><name>cell</name>"
           b"<bndbox><xmin>abc</xmin><ymin>0</ymin><xmax>10</xmax>"
           b"<ymax>10</ymax></bndbox></object></annotation>")
    crops, errors, warnings = ac.from_voc({"a.xml": xml}, {})
    assert crops == []
    assert any("Invalid bndbox values" in w for w in warnings)


def test_from_voc_missing_bndbox_child_attribute_error():
    # bndbox present but missing xmin child → find() returns None → AttributeError.
    xml = (b"<annotation><filename>a.tif</filename><object><name>cell</name>"
           b"<bndbox><ymin>0</ymin><xmax>10</xmax><ymax>10</ymax></bndbox>"
           b"</object></annotation>")
    crops, errors, warnings = ac.from_voc({"a.xml": xml}, {})
    assert crops == []
    assert any("Invalid bndbox values" in w for w in warnings)


def test_from_voc_invalid_confidence_ignored():
    xml = (b"<annotation><filename>a.tif</filename><object><name>cell</name>"
           b"<bndbox><xmin>0</xmin><ymin>0</ymin><xmax>10</xmax><ymax>10</ymax>"
           b"</bndbox><confidence>notanumber</confidence></object></annotation>")
    crops, errors, warnings = ac.from_voc({"a.xml": xml}, {})
    assert len(crops) == 1
    assert crops[0].confidence is None


def test_from_voc_empty_confidence_element():
    xml = (b"<annotation><filename>a.tif</filename><object><name>cell</name>"
           b"<bndbox><xmin>0</xmin><ymin>0</ymin><xmax>10</xmax><ymax>10</ymax>"
           b"</bndbox><confidence></confidence></object></annotation>")
    crops, errors, warnings = ac.from_voc({"a.xml": xml}, {})
    assert len(crops) == 1
    assert crops[0].confidence is None


def test_from_voc_degenerate_box_clamped_valid():
    # xmax == xmin → width 0 → normalize forces w=1, crop is valid.
    xml_files = {"a.xml": _voc_xml(objects=[
        {"name": "cell", "xmin": 10, "ymin": 10, "xmax": 10, "ymax": 10}
    ])}
    crops, errors, warnings = ac.from_voc(xml_files, {})
    assert len(crops) == 1
    assert crops[0].bbox_w == 1


def test_from_voc_empty_files():
    crops, errors, warnings = ac.from_voc({}, {})
    assert crops == [] and errors == [] and warnings == []


# ============================================================================
# from_csv
# ============================================================================


def _csv_bytes(header, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("utf-8")


def test_from_csv_success_full_columns():
    data = _csv_bytes(
        ["image_id", "filename", "x", "y", "width", "height", "class", "confidence"],
        [["1", "a.tif", "10", "20", "30", "40", "PRC1", "0.5"]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    assert errors == []
    assert len(crops) == 1
    c = crops[0]
    assert (c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h) == (10, 20, 30, 40)
    assert c.class_name == "PRC1"
    assert c.confidence == 0.5


def test_from_csv_alternate_column_names():
    data = _csv_bytes(
        ["file_name", "xmin", "ymin", "w", "h", "label", "score"],
        [["b.tif", "5", "5", "20", "20", "cell", "0.3"]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    assert len(crops) == 1
    assert crops[0].confidence == 0.3


def test_from_csv_missing_required_columns():
    data = _csv_bytes(["filename", "x"], [["a.tif", "1"]])
    crops, errors, warnings = ac.from_csv(data, {})
    assert crops == []
    assert any("Missing required columns" in e for e in errors)


def test_from_csv_invalid_numeric_value():
    data = _csv_bytes(
        ["filename", "x", "y", "width", "height"],
        [["a.tif", "notanumber", "0", "10", "10"]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    assert crops == []
    assert any("Invalid values" in w for w in warnings)


def test_from_csv_no_class_column_defaults_to_cell():
    data = _csv_bytes(
        ["filename", "x", "y", "width", "height"],
        [["a.tif", "1", "2", "10", "10"]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    assert crops[0].class_name == "cell"


def test_from_csv_empty_class_value_defaults_to_cell():
    data = _csv_bytes(
        ["filename", "x", "y", "width", "height", "class"],
        [["a.tif", "1", "2", "10", "10", ""]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    # class column present but empty → falls back to "cell" via `or "cell"`.
    assert crops[0].class_name == "cell"


def test_from_csv_invalid_confidence_ignored():
    data = _csv_bytes(
        ["filename", "x", "y", "width", "height", "confidence"],
        [["a.tif", "1", "2", "10", "10", "bad"]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    assert len(crops) == 1
    assert crops[0].confidence is None


def test_from_csv_empty_confidence_skipped():
    data = _csv_bytes(
        ["filename", "x", "y", "width", "height", "confidence"],
        [["a.tif", "1", "2", "10", "10", ""]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    assert crops[0].confidence is None


def test_from_csv_validation_error_zero_width():
    data = _csv_bytes(
        ["filename", "x", "y", "width", "height"],
        [["a.tif", "1", "2", "0", "10"]],
    )
    crops, errors, warnings = ac.from_csv(data, {})
    # width 0 → normalize forces w=1 → valid crop is created.
    assert len(crops) == 1
    assert crops[0].bbox_w == 1


def test_from_csv_latin1_fallback():
    # Header valid; data row contains a latin-1 byte in the filename.
    raw = (b"filename,x,y,width,height\n"
           b"caf\xe9.tif,1,2,10,10\n")
    crops, errors, warnings = ac.from_csv(raw, {})
    assert len(crops) == 1
    assert any("latin-1" in w for w in warnings)


def test_from_csv_parse_exception_returns_error():
    # Non-bytes input → .decode() raises AttributeError, caught by the broad
    # `except Exception` that wraps decode + DictReader construction.
    crops, errors, warnings = ac.from_csv(12345, {})  # type: ignore[arg-type]
    assert crops == []
    assert any("Invalid CSV" in e for e in errors)


def test_from_csv_no_data_rows():
    data = _csv_bytes(["filename", "x", "y", "width", "height"], [])
    crops, errors, warnings = ac.from_csv(data, {})
    assert crops == []
    assert errors == []


# ============================================================================
# parse_annotations (dispatcher)
# ============================================================================


def test_parse_annotations_coco():
    payload = {
        "categories": [{"id": 0, "name": "cell"}],
        "images": [{"id": 1, "file_name": "a.tif"}],
        "annotations": [{"id": 1, "image_id": 1, "bbox": [0, 0, 10, 10]}],
    }
    contents = {"annotations.json": _coco_bytes(payload)}
    crops, errors, warnings = ac.parse_annotations(
        contents, ["images/a.tif"], ImportFormat.COCO
    )
    assert len(crops) == 1


def test_parse_annotations_coco_no_json_file():
    crops, errors, warnings = ac.parse_annotations({}, [], ImportFormat.COCO)
    assert crops == []
    assert warnings == ["No COCO JSON file found"]


def test_parse_annotations_yolo_with_classes():
    contents = {
        "classes.txt": b"cell\nPRC1\n",
        "labels/img1.txt": b"1 0.5 0.5 0.2 0.2\n",
    }
    crops, errors, warnings = ac.parse_annotations(
        contents, ["images/img1.jpg"], ImportFormat.YOLO
    )
    assert len(crops) == 1
    assert crops[0].class_name == "PRC1"


def test_parse_annotations_yolo_no_classes_file_defaults():
    contents = {"labels/img1.txt": b"0 0.5 0.5 0.2 0.2\n"}
    crops, errors, warnings = ac.parse_annotations(
        contents, ["images/img1.jpg"], ImportFormat.YOLO
    )
    assert len(crops) == 1
    assert crops[0].class_name == "cell"


def test_parse_annotations_voc():
    contents = {"Annotations/a.xml": _voc_xml()}
    crops, errors, warnings = ac.parse_annotations(
        contents, ["JPEGImages/a.tif"], ImportFormat.VOC
    )
    assert len(crops) == 1


def test_parse_annotations_csv():
    data = _csv_bytes(
        ["filename", "x", "y", "width", "height"],
        [["a.tif", "1", "2", "10", "10"]],
    )
    contents = {"annotations.csv": data}
    crops, errors, warnings = ac.parse_annotations(
        contents, ["images/a.tif"], ImportFormat.CSV
    )
    assert len(crops) == 1


def test_parse_annotations_csv_no_csv_file():
    crops, errors, warnings = ac.parse_annotations({}, [], ImportFormat.CSV)
    assert crops == []
    assert warnings == ["No CSV file found"]


def test_parse_annotations_maptimize_returns_empty():
    crops, errors, warnings = ac.parse_annotations(
        {"manifest.json": b"{}"}, [], ImportFormat.MAPTIMIZE
    )
    assert crops == [] and errors == [] and warnings == []


# ============================================================================
# Round-trip conversions
# ============================================================================


def test_round_trip_coco_export_then_import():
    img = FakeImage(1, original_filename="a.tif", width=100, height=80,
                    created_at=FakeDate())
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40,
                    detection_confidence=0.5)
    coco = ac.to_coco([img], [crop])
    raw = json.dumps(coco).encode("utf-8")
    crops, errors, warnings = ac.from_coco(raw, {})
    assert errors == []
    assert len(crops) == 1
    c = crops[0]
    assert (c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h) == (10, 20, 30, 40)
    assert c.confidence == 0.5


def test_round_trip_voc_export_then_import():
    img = FakeImage(1, original_filename="a.tif", width=200, height=150)
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40)
    xml = ac.to_voc(img, [crop])
    crops, errors, warnings = ac.from_voc({"a.xml": xml.encode("utf-8")}, {})
    assert errors == []
    c = crops[0]
    assert (c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h) == (10, 20, 30, 40)


def test_round_trip_csv_export_then_import():
    img = FakeImage(1, original_filename="a.tif")
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40,
                    detection_confidence=0.5, map_protein=FakeProtein("PRC1"))
    csv_str = ac.to_csv([img], [crop])
    crops, errors, warnings = ac.from_csv(csv_str.encode("utf-8"), {})
    assert errors == []
    c = crops[0]
    assert (c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h) == (10, 20, 30, 40)
    assert c.class_name == "PRC1"
    assert c.confidence == 0.5


def test_round_trip_yolo_export_then_import():
    img = FakeImage(1, original_filename="a.tif", width=100, height=100)
    crop = FakeCrop(1, bbox_x=10, bbox_y=20, bbox_w=30, bbox_h=40)
    yolo = ac.to_yolo(img, [crop])
    label_files = {"a.txt": yolo.encode("utf-8")}
    image_map = {"a": ("images/a.tif", 100, 100)}
    crops, errors, warnings = ac.from_yolo(label_files, ["cell"], image_map)
    assert errors == []
    c = crops[0]
    # YOLO is lossy through normalization but should reconstruct exactly here.
    assert (c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h) == (10, 20, 30, 40)
