"""Make an ADK agent's A2UI output render in the ADK dev UI (`adk web`).

`adk web` has a built-in A2UI renderer, but it only fires when a response part is
a text/plain Blob wrapped in <a2a_datapart_json>...</a2a_datapart_json> with
custom_metadata {"a2a:response": "true"}. This callback takes the A2UI JSON the
model emits as text and rewraps it into exactly that format.

Wire it up with `after_model_callback=a2ui_callback` on your Agent. Pin the A2UI
schema to **version 0.8** (this callback keys off v0.8 messages: beginRendering,
surfaceUpdate, dataModelUpdate). Copy this file next to your agent.py.
"""

import json
import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types

# A2UI message kinds this renderer understands (v0.8).
_A2UI_KEYS = ("beginRendering", "surfaceUpdate", "dataModelUpdate", "deleteSurface")

# Tags the model may wrap around its output (its own render wrapper, or the SDK's).
_TAG_RE = re.compile(r"</?(?:a2a_datapart_json|a2ui-json)>")

# Valid A2UI v0.8 usageHint values for Text components
_VALID_USAGE_HINTS = {"h1", "h2", "body", "caption"}

# Shown when the model emitted A2UI we could not fully parse (usually malformed
# JSON on a large surface). Better than a blank card or a wall of raw JSON.
_FALLBACK_TEXT = (
    "I couldn't render that view. Could you ask again, maybe for a simpler summary?"
)

# adk web can only render an <Image> whose url is a real, fetchable http(s) link.
_HTTP_URL_RE = re.compile(r"^https?://", re.I)
_IMAGE_NOTE = "Image generated — open the Artifacts panel to view it."


def _wrap_a2ui_part(a2ui_message: dict) -> types.Part:
    """Wrap a single A2UI message for rendering in adk web."""
    datapart_json = json.dumps(
        {
            "kind": "data",
            "metadata": {"mimeType": "application/json+a2ui"},
            "data": a2ui_message,
        }
    )
    blob_data = (
        b"<a2a_datapart_json>" + datapart_json.encode("utf-8") + b"</a2a_datapart_json>"
    )
    return types.Part(
        inline_data=types.Blob(
            data=blob_data,
            mime_type="text/plain",
        )
    )


def _iter_json_values(text: str):
    """Yield top-level JSON values from a string of concatenated objects/arrays, resiliently."""
    decoder = json.JSONDecoder()
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] not in "{[":
            idx += 1
        if idx >= n:
            break
        try:
            value, end = decoder.raw_decode(text, idx)
            yield value
            idx = end
        except json.JSONDecodeError:
            idx += 1


def _extract_a2ui_messages(text: str) -> list[dict]:
    """Pull A2UI messages out of the model's raw text output."""
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    text = _TAG_RE.sub("", text).strip()

    values: list = []
    for value in _iter_json_values(text):
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)

    messages: list[dict] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        inner = value.get("data")
        if isinstance(inner, dict) and any(k in inner for k in _A2UI_KEYS):
            messages.append(inner)
        elif any(k in value for k in _A2UI_KEYS):
            messages.append(value)
    return messages


def _sanitize_image_components(messages: list[dict]) -> None:
    """Replace un-fetchable <Image> components with a short <Text> note, in place."""
    for m in messages:
        surface = m.get("surfaceUpdate")
        if not isinstance(surface, dict):
            continue
        for c in surface.get("components") or []:
            if not isinstance(c, dict):
                continue
            comp = c.get("component")
            if not isinstance(comp, dict) or "Image" not in comp:
                continue
            img = comp.get("Image")
            url = img.get("url") if isinstance(img, dict) else None
            literal = url.get("literalString") if isinstance(url, dict) else None
            if isinstance(literal, str) and _HTTP_URL_RE.match(literal):
                continue
            c["component"] = {
                "Text": {
                    "text": {"literalString": _IMAGE_NOTE},
                    "usageHint": "body",
                }
            }


def _sanitize_text_components(messages: list[dict]) -> None:
    """Ensure Text components strictly adhere to A2UI v0.8 schema: {"text": {"literalString": "..."}, "usageHint": ...}."""
    for m in messages:
        surface = m.get("surfaceUpdate")
        if not isinstance(surface, dict):
            continue
        for c in surface.get("components") or []:
            if not isinstance(c, dict):
                continue
            comp = c.get("component")
            if not isinstance(comp, dict) or "Text" not in comp:
                continue

            txt_spec = comp.get("Text")
            if not isinstance(txt_spec, dict):
                comp["Text"] = {"text": {"literalString": str(txt_spec)}, "usageHint": "body"}
                continue

            text_val = txt_spec.get("text")
            if isinstance(text_val, str):
                txt_spec["text"] = {"literalString": text_val}
            elif isinstance(text_val, dict):
                if "literalString" not in text_val:
                    str_val = next((str(v) for v in text_val.values() if isinstance(v, (str, int, float))), "")
                    txt_spec["text"] = {"literalString": str_val}
            else:
                txt_spec["text"] = {"literalString": str(text_val or "")}

            if "usageHint" in txt_spec:
                hint = str(txt_spec["usageHint"]).lower()
                if hint not in _VALID_USAGE_HINTS:
                    if "h" in hint or "title" in hint or "head" in hint:
                        txt_spec["usageHint"] = "h2"
                    else:
                        txt_spec["usageHint"] = "body"


def _sanitize_container_components(messages: list[dict]) -> None:
    """Ensure Card, Column, and Row components strictly adhere to A2UI v0.8 schema."""
    for m in messages:
        su = m.get("surfaceUpdate")
        if not isinstance(su, dict):
            continue
        for c in su.get("components") or []:
            if not isinstance(c, dict):
                continue
            comp = c.get("component")
            if not isinstance(comp, dict):
                continue

            if "Card" in comp:
                card_spec = comp["Card"]
                if not isinstance(card_spec, dict):
                    comp["Card"] = {"child": str(card_spec)}
                else:
                    if "child" not in card_spec:
                        if "children" in card_spec:
                            children = card_spec["children"]
                            if isinstance(children, list) and children:
                                card_spec["child"] = str(children[0])
                            elif isinstance(children, dict) and isinstance(children.get("explicitList"), list) and children["explicitList"]:
                                card_spec["child"] = str(children["explicitList"][0])
                            card_spec.pop("children", None)

            for container_name in ("Column", "Row"):
                if container_name in comp:
                    spec = comp[container_name]
                    if not isinstance(spec, dict):
                        comp[container_name] = {"children": {"explicitList": []}}
                    else:
                        children = spec.get("children")
                        if isinstance(children, list):
                            spec["children"] = {"explicitList": [str(x) for x in children]}
                        elif isinstance(children, dict):
                            ex_list = children.get("explicitList")
                            if isinstance(ex_list, list):
                                children["explicitList"] = [str(x) for x in ex_list]
                            else:
                                children["explicitList"] = []
                        elif "child" in spec:
                            spec["children"] = {"explicitList": [str(spec["child"])]}
                            spec.pop("child", None)
                        else:
                            spec["children"] = {"explicitList": []}


def _component_ids_and_refs(components: list) -> tuple[set, set]:
    """Return (defined ids, referenced child ids) for a component list."""
    ids: set = set()
    refs: set = set()
    for c in components:
        if not isinstance(c, dict):
            continue
        if "id" in c:
            ids.add(c["id"])
        comp = c.get("component")
        if not isinstance(comp, dict):
            continue
        for spec in comp.values():
            if not isinstance(spec, dict):
                continue
            if isinstance(spec.get("child"), str):
                refs.add(spec["child"])
            children = spec.get("children")
            if isinstance(children, dict):
                for cid in children.get("explicitList") or []:
                    if isinstance(cid, str):
                        refs.add(cid)
    return ids, refs


def _repair_a2ui_messages(messages: list[dict]) -> None:
    """Harmonize surfaceIds, synthesize missing beginRendering, and repair mismatched root or child references."""
    if not messages:
        return

    # 1. Harmonize surfaceIds across all messages
    target_surface_id = None
    for m in messages:
        if "surfaceUpdate" in m and isinstance(m["surfaceUpdate"], dict):
            target_surface_id = m["surfaceUpdate"].get("surfaceId")
            if target_surface_id:
                break
        elif "beginRendering" in m and isinstance(m["beginRendering"], dict):
            target_surface_id = m["beginRendering"].get("surfaceId")
            if target_surface_id:
                break
    if not target_surface_id:
        target_surface_id = "main_surface"

    for m in messages:
        for k in ("beginRendering", "surfaceUpdate", "dataModelUpdate", "deleteSurface"):
            if k in m and isinstance(m[k], dict):
                m[k]["surfaceId"] = target_surface_id

    # 2. Gather component IDs and child references
    components = []
    for m in messages:
        su = m.get("surfaceUpdate")
        if isinstance(su, dict):
            components = su.get("components") or []
            break

    all_ids, all_refs = _component_ids_and_refs(components)

    # 3. Ensure beginRendering exists if components are present
    has_begin = any("beginRendering" in m for m in messages)
    if components and not has_begin:
        unreferenced = all_ids - all_refs
        root_id = list(unreferenced)[0] if unreferenced else (components[0].get("id") if components else "card_root")
        messages.insert(0, {"beginRendering": {"surfaceId": target_surface_id, "root": root_id}})

    # 4. Repair root in beginRendering if it points to an undefined component ID
    if all_ids:
        unreferenced = all_ids - all_refs
        valid_root = list(unreferenced)[0] if unreferenced else list(all_ids)[0]
        for m in messages:
            br = m.get("beginRendering")
            if isinstance(br, dict):
                curr_root = br.get("root")
                if not curr_root or curr_root not in all_ids:
                    br["root"] = valid_root

    # 5. Remove dangling child references from explicitList
    if all_ids:
        for m in messages:
            su = m.get("surfaceUpdate")
            if isinstance(su, dict) and su.get("components"):
                for c in su["components"]:
                    if isinstance(c, dict) and isinstance(c.get("component"), dict):
                        for spec in c["component"].values():
                            if isinstance(spec, dict):
                                children = spec.get("children")
                                if isinstance(children, dict) and isinstance(children.get("explicitList"), list):
                                    children["explicitList"] = [
                                        cid for cid in children["explicitList"] if cid in all_ids
                                    ]


def _surface_is_renderable(messages: list[dict]) -> bool:
    """True only if the messages form a surface adk web can actually draw."""
    all_ids: set = set()
    all_refs: set = set()
    roots: list = []
    has_body = False
    for m in messages:
        if "dataModelUpdate" in m or "deleteSurface" in m:
            return True
        br = m.get("beginRendering")
        if isinstance(br, dict) and isinstance(br.get("root"), str):
            roots.append(br["root"])
        su = m.get("surfaceUpdate")
        if isinstance(su, dict) and su.get("components"):
            has_body = True
            ids, refs = _component_ids_and_refs(su["components"])
            all_ids |= ids
            all_refs |= refs
    if not has_body:
        return False
    if any(root not in all_ids for root in roots):
        return False
    if all_refs - all_ids:
        return False
    return True


def a2ui_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Convert A2UI JSON in text output to rendered components (or a clean fallback)."""
    if not llm_response.content or not llm_response.content.parts:
        return None

    all_messages: list[dict] = []
    text_parts: list[types.Part] = []

    for part in llm_response.content.parts:
        text = (part.text or "").strip()
        if not text:
            continue

        if any(k in text for k in _A2UI_KEYS):
            msgs = _extract_a2ui_messages(text)
            if msgs:
                all_messages.extend(msgs)
            else:
                # If tags or A2UI keys were present but couldn't be extracted, strip tags and keep text
                cleaned = _TAG_RE.sub("", text).strip()
                if cleaned:
                    text_parts.append(types.Part(text=cleaned))
        else:
            text_parts.append(types.Part(text=text))

    if not all_messages:
        if text_parts:
            return LlmResponse(content=types.Content(role="model", parts=text_parts))
        return None

    _repair_a2ui_messages(all_messages)
    _sanitize_image_components(all_messages)
    _sanitize_text_components(all_messages)
    _sanitize_container_components(all_messages)

    if not _surface_is_renderable(all_messages):
        # Surface is not fully renderable: fall back to natural text response rather than error box
        if text_parts:
            return LlmResponse(content=types.Content(role="model", parts=text_parts))
        return None

    new_parts = list(text_parts) + [_wrap_a2ui_part(m) for m in all_messages]
    return LlmResponse(
        content=types.Content(role="model", parts=new_parts),
        custom_metadata={"a2a:response": "true"},
    )

