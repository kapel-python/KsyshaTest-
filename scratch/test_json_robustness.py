import json
from typing import Tuple

def current_extract_json_block(text: str) -> Tuple[str, dict | None]:
    text_len = len(text)
    first_brace = text.find("{")
    if first_brace == -1:
        return text, None

    for i in range(text_len - 1, -1, -1):
        if text[i] == "}":
            brace_count = 0
            for j in range(i, -1, -1):
                if text[j] == "}":
                    brace_count += 1
                elif text[j] == "{":
                    brace_count -= 1
                    if brace_count == 0:
                        candidate = text[j:i+1].strip()
                        try:
                            if candidate.startswith("```json"):
                                candidate = candidate[7:].strip()
                            elif candidate.startswith("```"):
                                candidate = candidate[3:].strip()
                            if candidate.endswith("```"):
                                candidate = candidate[:-3].strip()
                            
                            obj = json.loads(candidate)
                            if isinstance(obj, dict) and ("tool" in obj or "answer" in obj):
                                left_text = (text[:j].strip() + "\n" + text[i+1:].strip()).strip()
                                return left_text, obj
                        except Exception:
                            pass
    return text, None

def improved_extract_json_block(text: str) -> Tuple[str, dict | None]:
    idx = 0
    while True:
        first_brace = text.find("{", idx)
        if first_brace == -1:
            break
        try:
            # We try to parse JSON from this '{' onwards
            obj, end_idx = json.JSONDecoder().raw_decode(text[first_brace:])
            if isinstance(obj, dict) and ("tool" in obj or "answer" in obj):
                # Clean up any trailing backticks or markdown fences around the JSON block
                before_json = text[:first_brace].strip()
                after_json = text[first_brace + end_idx:].strip()
                
                # Strip markdown code block wrappers if they enclose the JSON block
                if before_json.endswith("```json"):
                    before_json = before_json[:-7].strip()
                elif before_json.endswith("```"):
                    before_json = before_json[:-3].strip()
                if after_json.startswith("```"):
                    after_json = after_json[3:].strip()
                    
                left_text = (before_json + "\n" + after_json).strip()
                return left_text, obj
        except json.JSONDecodeError:
            pass
        idx = first_brace + 1
    return text, None

# Test cases
test_cases = [
    # 1. Simple JSON
    '{"tool": null, "answer": "Hello"}',
    # 2. Leading and trailing text
    'Hello user!\n{"tool": null, "answer": "Hello"}\nHope you like it!',
    # 3. Unmatched curly braces in string
    'Some text...\n{"tool": null, "answer": "Here is an opening brace { that does not match.", "suggestions": []}\nTrailing text...',
    # 4. Markdown wrapped JSON
    'Yes, I can help!\n```json\n{"tool": null, "answer": "Markdown version"}\n```\nGoodbye!',
]

print("--- Testing current_extract_json_block ---")
for idx, tc in enumerate(test_cases, 1):
    left, obj = current_extract_json_block(tc)
    print(f"Test {idx}:")
    print(f"  Parsed successfully: {obj is not None}")
    print(f"  Answer: {obj.get('answer') if obj else None}")
    print(f"  Leftover text: {repr(left)}")

print("\n--- Testing improved_extract_json_block ---")
for idx, tc in enumerate(test_cases, 1):
    left, obj = improved_extract_json_block(tc)
    print(f"Test {idx}:")
    print(f"  Parsed successfully: {obj is not None}")
    print(f"  Answer: {obj.get('answer') if obj else None}")
    print(f"  Leftover text: {repr(left)}")
