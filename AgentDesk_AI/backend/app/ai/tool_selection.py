"""Keep common request prompts small without weakening registry validation."""
import re


def tool_metadata(registry, command):
    text = command.casefold()
    groups = [
        (r"download|install|package", {"application_package", "download_web_content", "web_search", "open_url"}),
        (r"email|e-mail|compose|draft", {"compose_email", "open_application"}),
        (r"brightness|volume|bluetooth|awaz|roshni", {"manage_system_setting"}),
        (r"copy|move|rename|delete|trash|restore|folder|file|document|zip|extract", {"find_paths", "find_files", "list_folder", "open_folder", "open_file"}),
        (r"create|folder|directory", {"create_folder"}),
        (r"copy", {"copy_file"}), (r"move", {"move_file"}),
        (r"rename", {"rename_file"}), (r"delete|trash", {"delete_file", "list_trash"}),
        (r"restore", {"restore_file", "list_trash"}),
        (r"write|edit|read|text", {"write_text_file", "edit_text_file", "read_text_file"}),
        (r"compress|zip", {"compress_folder"}), (r"extract|unzip", {"extract_file"}),
        (r"open|close|window|tab|minimize|maximize|click|type|press|scroll", {"open_application", "control_desktop"}),
        (r"search|website|browser|youtube|play", {"web_search", "open_url", "play_youtube_video"}),
        (r"tab|browser|youtube|video|pause|resume", {"control_browser"}),
        (r"screenshot", {"take_screenshot"}),
        (r"remember|forget|memory", {"memory_remember", "memory_recall", "memory_forget"}),
        (r"summari[sz]e|summary|news|latest|weather|who|what|why|how", {"web_answer", "summarise_page", "summarise_file", "read_text_file"}),
    ]
    selected = set()
    for pattern, names in groups:
        if re.search(r"\b(?:" + pattern + r")\b", text):
            selected.update(names)
    # Unrecognized languages, confirmations and requests retain the full catalogue.
    metadata = registry.metadata()
    return [item for item in metadata if item["name"] in selected] if selected else metadata
