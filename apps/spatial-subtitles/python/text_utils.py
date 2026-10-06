def clean_text(value, limit=360):
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c for c in value if c >= " " or c in "\n\t").split())[
        :limit
    ]
