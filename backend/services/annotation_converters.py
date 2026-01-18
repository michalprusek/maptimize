"""
Annotation format converters for export/import.

Supports bidirectional conversion between:
- COCO JSON format
- YOLO TXT format
- Pascal VOC XML format
- CSV format
- Maptimize native format

All converters work with the internal CropImportData structure for imports
and database models for exports.
"""
import csv
import io
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.dom import minidom

from schemas.export_import import CropImportData, ImportFormat

logger = logging.getLogger(__name__)


# ============================================================================
# Export Converters - Database Models → Annotation Files
# ============================================================================


def to_coco(
    images: List[Any],
    crops: List[Any],
    categories: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Convert images and crops to COCO JSON format.

    COCO format uses [x, y, width, height] in absolute pixels.

    Args:
        images: List of Image model instances
        crops: List of CellCrop model instances
        categories: Optional category definitions, defaults to single "cell" class

    Returns:
        COCO format dictionary ready for JSON serialization
    """
    if categories is None:
        categories = [{"id": 0, "name": "cell", "supercategory": "object"}]

    coco_images = []
    coco_annotations = []
    annotation_id = 1

    # Build image ID mapping
    image_id_map = {img.id: idx + 1 for idx, img in enumerate(images)}

    for img in images:
        coco_img_id = image_id_map[img.id]

        # Use original filename for COCO
        filename = img.original_filename
        if img.mip_path:
            # Use MIP filename if available
            filename = os.path.basename(img.mip_path)

        coco_images.append({
            "id": coco_img_id,
            "file_name": filename,
            "width": img.width or 0,
            "height": img.height or 0,
            "date_captured": img.created_at.isoformat() if img.created_at else None,
        })

    # Group crops by image
    crops_by_image = {}
    for crop in crops:
        if crop.image_id not in crops_by_image:
            crops_by_image[crop.image_id] = []
        crops_by_image[crop.image_id].append(crop)

    for img in images:
        coco_img_id = image_id_map[img.id]
        img_crops = crops_by_image.get(img.id, [])

        for crop in img_crops:
            # COCO bbox: [x, y, width, height]
            bbox = [crop.bbox_x, crop.bbox_y, crop.bbox_w, crop.bbox_h]
            area = crop.bbox_w * crop.bbox_h

            annotation = {
                "id": annotation_id,
                "image_id": coco_img_id,
                "category_id": 0,  # "cell" category
                "bbox": bbox,
                "area": area,
                "iscrowd": 0,
            }

            # Add optional fields
            if crop.detection_confidence is not None:
                annotation["score"] = crop.detection_confidence

            # Add protein class if available
            if crop.map_protein_id and crop.map_protein:
                annotation["attributes"] = {
                    "protein": crop.map_protein.name
                }

            coco_annotations.append(annotation)
            annotation_id += 1

    return {
        "info": {
            "description": "MAPtimize cell detection export",
            "version": "1.0",
            "date_created": datetime.now(timezone.utc).isoformat(),
        },
        "licenses": [],
        "categories": categories,
        "images": coco_images,
        "annotations": coco_annotations,
    }


def to_yolo(
    image: Any,
    crops: List[Any],
    class_names: Optional[List[str]] = None
) -> str:
    """
    Convert image crops to YOLO TXT format.

    YOLO format: class x_center y_center width height (all normalized 0-1)
    One line per bounding box.

    Args:
        image: Image model instance
        crops: List of CellCrop model instances for this image
        class_names: List of class names (index = class id), defaults to ["cell"]

    Returns:
        YOLO format string (one annotation per line)
    """
    if class_names is None:
        class_names = ["cell"]

    if not image.width or not image.height:
        logger.warning(f"Image {image.id} missing dimensions, using bbox estimates")
        # Estimate dimensions from crops
        max_x = max((c.bbox_x + c.bbox_w for c in crops), default=512)
        max_y = max((c.bbox_y + c.bbox_h for c in crops), default=512)
        img_w, img_h = max_x, max_y
    else:
        img_w, img_h = image.width, image.height

    lines = []
    for crop in crops:
        # Calculate center and normalize
        x_center = (crop.bbox_x + crop.bbox_w / 2) / img_w
        y_center = (crop.bbox_y + crop.bbox_h / 2) / img_h
        width = crop.bbox_w / img_w
        height = crop.bbox_h / img_h

        # Determine class ID
        class_id = 0  # Default to first class
        if crop.map_protein and crop.map_protein.name in class_names:
            class_id = class_names.index(crop.map_protein.name)

        # YOLO format: class x_center y_center width height
        lines.append(f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")

    return "\n".join(lines)


def to_yolo_classes(class_names: Optional[List[str]] = None) -> str:
    """
    Generate YOLO classes.txt content.

    Args:
        class_names: List of class names, defaults to ["cell"]

    Returns:
        classes.txt content (one class per line)
    """
    if class_names is None:
        class_names = ["cell"]
    return "\n".join(class_names)


def to_voc(
    image: Any,
    crops: List[Any],
    folder: str = "images"
) -> str:
    """
    Convert image crops to Pascal VOC XML format.

    VOC format uses <bndbox> with xmin, ymin, xmax, ymax in absolute pixels.

    Args:
        image: Image model instance
        crops: List of CellCrop model instances for this image
        folder: Folder name for the annotation

    Returns:
        VOC XML string
    """
    # Use MIP filename if available
    filename = image.original_filename
    if image.mip_path:
        filename = os.path.basename(image.mip_path)

    img_w = image.width or 512
    img_h = image.height or 512

    # Build XML structure
    root = ET.Element("annotation")

    ET.SubElement(root, "folder").text = folder
    ET.SubElement(root, "filename").text = filename

    source = ET.SubElement(root, "source")
    ET.SubElement(source, "database").text = "MAPtimize"

    size = ET.SubElement(root, "size")
    ET.SubElement(size, "width").text = str(img_w)
    ET.SubElement(size, "height").text = str(img_h)
    ET.SubElement(size, "depth").text = "1"  # Grayscale

    ET.SubElement(root, "segmented").text = "0"

    for crop in crops:
        obj = ET.SubElement(root, "object")

        # Class name
        name = "cell"
        if crop.map_protein:
            name = crop.map_protein.name
        ET.SubElement(obj, "name").text = name

        ET.SubElement(obj, "pose").text = "Unspecified"
        ET.SubElement(obj, "truncated").text = "0"
        ET.SubElement(obj, "difficult").text = "0"

        bndbox = ET.SubElement(obj, "bndbox")
        ET.SubElement(bndbox, "xmin").text = str(crop.bbox_x)
        ET.SubElement(bndbox, "ymin").text = str(crop.bbox_y)
        ET.SubElement(bndbox, "xmax").text = str(crop.bbox_x + crop.bbox_w)
        ET.SubElement(bndbox, "ymax").text = str(crop.bbox_y + crop.bbox_h)

        # Optional confidence score
        if crop.detection_confidence is not None:
            ET.SubElement(obj, "confidence").text = f"{crop.detection_confidence:.4f}"

    # Pretty print XML
    xml_str = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(xml_str)
    return dom.toprettyxml(indent="  ")


def to_csv(
    images: List[Any],
    crops: List[Any]
) -> str:
    """
    Convert images and crops to CSV format.

    CSV columns: image_id, filename, x, y, width, height, class, confidence

    Args:
        images: List of Image model instances
        crops: List of CellCrop model instances

    Returns:
        CSV string with header
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "image_id", "filename", "x", "y", "width", "height", "class", "confidence"
    ])

    # Build image lookup
    image_map = {img.id: img for img in images}

    for crop in crops:
        img = image_map.get(crop.image_id)
        if not img:
            continue

        filename = img.original_filename
        if img.mip_path:
            filename = os.path.basename(img.mip_path)

        class_name = "cell"
        if crop.map_protein:
            class_name = crop.map_protein.name

        confidence = crop.detection_confidence if crop.detection_confidence else ""

        writer.writerow([
            img.id,
            filename,
            crop.bbox_x,
            crop.bbox_y,
            crop.bbox_w,
            crop.bbox_h,
            class_name,
            confidence
        ])

    return output.getvalue()


# ============================================================================
# Import Converters - Annotation Files → CropImportData
# ============================================================================


def detect_import_format(zip_contents: Dict[str, bytes]) -> ImportFormat:
    """
    Detect the annotation format from ZIP file contents.

    Detection logic:
    - manifest.json → MAPTIMIZE
    - annotations.json or coco.json → COCO
    - classes.txt or labels/*.txt → YOLO
    - Annotations/*.xml → VOC
    - annotations.csv → CSV

    Args:
        zip_contents: Dictionary of filename → file contents

    Returns:
        Detected ImportFormat
    """
    filenames = set(zip_contents.keys())
    filenames_lower = {f.lower() for f in filenames}

    # Check for Maptimize native format
    if "manifest.json" in filenames_lower or any("manifest.json" in f.lower() for f in filenames):
        return ImportFormat.MAPTIMIZE

    # Check for COCO format
    if any(f.endswith("annotations.json") or f.endswith("coco.json") for f in filenames_lower):
        return ImportFormat.COCO

    # Check for YOLO format (has classes.txt or labels folder)
    has_classes = any("classes.txt" in f.lower() for f in filenames)
    has_labels = any("/labels/" in f or f.startswith("labels/") for f in filenames)
    has_txt_labels = any(f.endswith(".txt") and "classes" not in f.lower() for f in filenames)
    if has_classes or (has_labels and has_txt_labels):
        return ImportFormat.YOLO

    # Check for Pascal VOC format
    has_xml = any(f.endswith(".xml") for f in filenames)
    has_annotations_folder = any("/annotations/" in f.lower() or f.lower().startswith("annotations/") for f in filenames)
    if has_xml and has_annotations_folder:
        return ImportFormat.VOC

    # Check for CSV format
    if any(f.endswith(".csv") for f in filenames_lower):
        return ImportFormat.CSV

    # Default to COCO if we can't detect
    logger.warning("Could not detect import format, defaulting to COCO")
    return ImportFormat.COCO


def from_coco(
    annotations_json: bytes,
    image_map: Dict[str, str]
) -> Tuple[List[CropImportData], List[str], List[str]]:
    """
    Parse COCO format annotations.

    Args:
        annotations_json: Raw JSON bytes
        image_map: Dict of filename → image path in ZIP

    Returns:
        Tuple of (crops, errors, warnings)
    """
    errors = []
    warnings = []
    crops = []

    try:
        data = json.loads(annotations_json.decode("utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON: {e}")
        return crops, errors, warnings

    # Build category lookup
    categories = {c["id"]: c["name"] for c in data.get("categories", [])}
    if not categories:
        categories = {0: "cell"}
        warnings.append("No categories found, using default 'cell' class")

    # Build image ID → filename lookup
    coco_images = {img["id"]: img for img in data.get("images", [])}

    for ann in data.get("annotations", []):
        image_id = ann.get("image_id")
        if image_id not in coco_images:
            warnings.append(f"Annotation {ann.get('id')} references unknown image {image_id}")
            continue

        coco_img = coco_images[image_id]
        filename = coco_img.get("file_name", "")

        bbox = ann.get("bbox", [])
        if len(bbox) != 4:
            warnings.append(f"Invalid bbox for annotation {ann.get('id')}")
            continue

        # COCO bbox: [x, y, width, height]
        x, y, w, h = [int(round(v)) for v in bbox]

        # Get class name
        category_id = ann.get("category_id", 0)
        class_name = categories.get(category_id, "cell")

        # Get confidence
        confidence = ann.get("score")

        crops.append(CropImportData(
            image_filename=filename,
            bbox_x=x,
            bbox_y=y,
            bbox_w=w,
            bbox_h=h,
            class_name=class_name,
            confidence=confidence
        ))

    return crops, errors, warnings


def from_yolo(
    label_files: Dict[str, bytes],
    classes: List[str],
    image_map: Dict[str, Tuple[str, int, int]]
) -> Tuple[List[CropImportData], List[str], List[str]]:
    """
    Parse YOLO format annotations.

    Args:
        label_files: Dict of label filename → content
        classes: List of class names from classes.txt
        image_map: Dict of label filename stem → (image path, width, height)

    Returns:
        Tuple of (crops, errors, warnings)
    """
    errors = []
    warnings = []
    crops = []

    for label_file, content in label_files.items():
        # Get corresponding image
        stem = Path(label_file).stem
        if stem not in image_map:
            warnings.append(f"No image found for label file {label_file}")
            continue

        image_path, img_w, img_h = image_map[stem]
        image_filename = os.path.basename(image_path)

        lines = content.decode("utf-8").strip().split("\n")
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) < 5:
                warnings.append(f"{label_file}:{line_num} - Invalid format")
                continue

            try:
                class_id = int(parts[0])
                x_center = float(parts[1])
                y_center = float(parts[2])
                width = float(parts[3])
                height = float(parts[4])
            except ValueError:
                warnings.append(f"{label_file}:{line_num} - Invalid values")
                continue

            # Denormalize to absolute pixels
            bbox_w = int(round(width * img_w))
            bbox_h = int(round(height * img_h))
            bbox_x = int(round(x_center * img_w - bbox_w / 2))
            bbox_y = int(round(y_center * img_h - bbox_h / 2))

            # Clamp to image bounds
            bbox_x = max(0, bbox_x)
            bbox_y = max(0, bbox_y)

            # Get class name
            class_name = classes[class_id] if class_id < len(classes) else "cell"

            # Optional confidence (6th field)
            confidence = float(parts[5]) if len(parts) > 5 else None

            crops.append(CropImportData(
                image_filename=image_filename,
                bbox_x=bbox_x,
                bbox_y=bbox_y,
                bbox_w=bbox_w,
                bbox_h=bbox_h,
                class_name=class_name,
                confidence=confidence
            ))

    return crops, errors, warnings


def from_voc(
    xml_files: Dict[str, bytes],
    image_map: Dict[str, str]
) -> Tuple[List[CropImportData], List[str], List[str]]:
    """
    Parse Pascal VOC format annotations.

    Args:
        xml_files: Dict of XML filename → content
        image_map: Dict of image filename → image path in ZIP

    Returns:
        Tuple of (crops, errors, warnings)
    """
    errors = []
    warnings = []
    crops = []

    for xml_file, content in xml_files.items():
        try:
            root = ET.fromstring(content.decode("utf-8"))
        except ET.ParseError as e:
            errors.append(f"Invalid XML in {xml_file}: {e}")
            continue

        # Get filename from XML
        filename_elem = root.find("filename")
        if filename_elem is None or not filename_elem.text:
            warnings.append(f"No filename in {xml_file}")
            continue

        filename = filename_elem.text

        # Parse objects
        for obj in root.findall("object"):
            name_elem = obj.find("name")
            class_name = name_elem.text if name_elem is not None else "cell"

            bndbox = obj.find("bndbox")
            if bndbox is None:
                warnings.append(f"No bndbox in {xml_file} object")
                continue

            try:
                xmin = int(float(bndbox.find("xmin").text))
                ymin = int(float(bndbox.find("ymin").text))
                xmax = int(float(bndbox.find("xmax").text))
                ymax = int(float(bndbox.find("ymax").text))
            except (AttributeError, ValueError) as e:
                warnings.append(f"Invalid bndbox values in {xml_file}: {e}")
                continue

            # VOC uses xmin/ymin/xmax/ymax, convert to x/y/w/h
            bbox_x = xmin
            bbox_y = ymin
            bbox_w = xmax - xmin
            bbox_h = ymax - ymin

            # Optional confidence
            confidence = None
            conf_elem = obj.find("confidence")
            if conf_elem is not None and conf_elem.text:
                try:
                    confidence = float(conf_elem.text)
                except ValueError:
                    pass

            crops.append(CropImportData(
                image_filename=filename,
                bbox_x=bbox_x,
                bbox_y=bbox_y,
                bbox_w=bbox_w,
                bbox_h=bbox_h,
                class_name=class_name,
                confidence=confidence
            ))

    return crops, errors, warnings


def from_csv(
    csv_data: bytes,
    image_map: Dict[str, str]
) -> Tuple[List[CropImportData], List[str], List[str]]:
    """
    Parse CSV format annotations.

    Expected columns: image_id, filename, x, y, width, height, class, confidence
    Or minimal: filename, x, y, width, height

    Args:
        csv_data: Raw CSV bytes
        image_map: Dict of filename → image path in ZIP

    Returns:
        Tuple of (crops, errors, warnings)
    """
    errors = []
    warnings = []
    crops = []

    try:
        content = csv_data.decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
    except Exception as e:
        errors.append(f"Invalid CSV: {e}")
        return crops, errors, warnings

    fieldnames = reader.fieldnames or []

    # Detect column names
    filename_col = next((f for f in fieldnames if f.lower() in ("filename", "file_name", "image")), None)
    x_col = next((f for f in fieldnames if f.lower() in ("x", "xmin", "bbox_x")), None)
    y_col = next((f for f in fieldnames if f.lower() in ("y", "ymin", "bbox_y")), None)
    w_col = next((f for f in fieldnames if f.lower() in ("width", "w", "bbox_w")), None)
    h_col = next((f for f in fieldnames if f.lower() in ("height", "h", "bbox_h")), None)
    class_col = next((f for f in fieldnames if f.lower() in ("class", "class_name", "category", "label")), None)
    conf_col = next((f for f in fieldnames if f.lower() in ("confidence", "score", "conf")), None)

    if not all([filename_col, x_col, y_col, w_col, h_col]):
        errors.append(f"Missing required columns. Found: {fieldnames}")
        return crops, errors, warnings

    for row_num, row in enumerate(reader, 2):
        try:
            filename = row[filename_col]
            x = int(float(row[x_col]))
            y = int(float(row[y_col]))
            w = int(float(row[w_col]))
            h = int(float(row[h_col]))
        except (KeyError, ValueError) as e:
            warnings.append(f"Row {row_num}: Invalid values - {e}")
            continue

        class_name = row.get(class_col, "cell") if class_col else "cell"

        confidence = None
        if conf_col and row.get(conf_col):
            try:
                confidence = float(row[conf_col])
            except ValueError:
                pass

        crops.append(CropImportData(
            image_filename=filename,
            bbox_x=x,
            bbox_y=y,
            bbox_w=w,
            bbox_h=h,
            class_name=class_name or "cell",
            confidence=confidence
        ))

    return crops, errors, warnings


# ============================================================================
# Unified Annotation Parsing (DRY helper)
# ============================================================================


def parse_annotations(
    zip_contents: Dict[str, bytes],
    image_files: List[str],
    import_format: ImportFormat,
) -> Tuple[List[CropImportData], List[str], List[str]]:
    """
    Parse annotations from ZIP contents based on detected format.

    This is a DRY helper that unifies annotation parsing logic used
    in both validation and import execution.

    Args:
        zip_contents: Dict of annotation filename → content
        image_files: List of image file paths in ZIP
        import_format: The format to parse

    Returns:
        Tuple of (crops, errors, warnings)
    """
    if import_format == ImportFormat.COCO:
        coco_key = next(
            (k for k in zip_contents if k.endswith(".json") and ("coco" in k.lower() or k.endswith("annotations.json"))),
            None
        )
        if coco_key:
            image_map = {os.path.basename(f): f for f in image_files}
            return from_coco(zip_contents[coco_key], image_map)
        return [], [], ["No COCO JSON file found"]

    if import_format == ImportFormat.YOLO:
        classes_key = next(
            (k for k in zip_contents if k.endswith("classes.txt")),
            None
        )
        classes = ["cell"]
        if classes_key:
            classes = zip_contents[classes_key].decode("utf-8").strip().split("\n")

        label_files = {
            k: v for k, v in zip_contents.items()
            if k.endswith(".txt") and "classes" not in k.lower()
        }

        # Build image map with default dimensions (corrected during import)
        image_map = {}
        for img_file in image_files:
            stem = Path(img_file).stem
            image_map[stem] = (img_file, 512, 512)

        return from_yolo(label_files, classes, image_map)

    if import_format == ImportFormat.VOC:
        xml_files = {
            k: v for k, v in zip_contents.items()
            if k.endswith(".xml")
        }
        image_map = {os.path.basename(f): f for f in image_files}
        return from_voc(xml_files, image_map)

    if import_format == ImportFormat.CSV:
        csv_key = next(
            (k for k in zip_contents if k.endswith(".csv")),
            None
        )
        if csv_key:
            image_map = {os.path.basename(f): f for f in image_files}
            return from_csv(zip_contents[csv_key], image_map)
        return [], [], ["No CSV file found"]

    # MAPTIMIZE format - handled separately as it uses manifest
    return [], [], []
