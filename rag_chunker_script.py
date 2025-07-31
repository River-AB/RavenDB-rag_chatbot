import os
import re
from pyravendb.store.document_store import DocumentStore
from pathlib import Path
import frontmatter

# --- LIBRARIES TO INSTALL ---
# pip install pyravendb python-frontmatter

# --- CONFIGURATION ---
RAVEN_URL = "http://localhost:8080"
DATABASE_NAME = "RAG_Chatbot"
COLLECTION_NAME = "Context"
MARKDOWN_DIR = Path.cwd() / "markdown_files" # Directory containing your .md files

# --- CHUNKING PARAMETERS ---
CHUNK_SIZE = 3000      # Updated chunk size
CHUNK_OVERLAP = 450    # Updated, hefty overlap

# --- DOCUMENT CLASS DEFINITION ---
# This class provides a clear structure for our documents, fixing the error.
class ContextChunk:
    def __init__(self, title=None, content=None, source_file=None, chunk_number=None):
        self.Title = title
        self.Content = content
        self.SourceFile = source_file
        self.ChunkNumber = chunk_number

def prepare_markdown_content(markdown_text: str) -> str:
    """
    Performs light cleaning on raw markdown text by removing only the
    front matter and normalizing excessive whitespace. The markdown
    structure (headings, code blocks, lists) is preserved.

    Args:
        markdown_text: The raw Markdown text from a file.

    Returns:
        The markdown content with front matter removed.
    """
    # 1. Remove front matter (e.g., title, author, date metadata)
    try:
        # The `content` attribute contains the main markdown body
        content = frontmatter.loads(markdown_text).content
    except Exception:
        # If frontmatter parsing fails, fall back to using the original text
        content = markdown_text

    # 2. Normalize excessive whitespace to ensure consistent chunking
    # This collapses more than two consecutive newlines into just two.
    content = re.sub(r'\n{3,}', '\n\n', content)

    return content.strip()

def process_and_chunk_file(store: DocumentStore, md_file: Path):
    """
    Reads, lightly cleans, and chunks a single markdown file,
    then uploads the markdown chunks to RavenDB.

    Args:
        store: An initialized RavenDB DocumentStore instance.
        md_file: The path to the markdown file to process.

    Returns:
        The number of chunks successfully created and stored from the file.
    """
    print(f"\n{'='*15} Processing File: {md_file.name} {'='*15}")
    
    try:
        with open(md_file, "r", encoding="utf-8") as f:
            raw_content = f.read()

        if not raw_content.strip():
            print("  - LOG: File is empty or contains only whitespace. Skipping.")
            return 0

        # 1. Prepare the markdown content (remove front matter, etc.)
        print("  - LOG: Preparing markdown content (preserving structure)...")
        markdown_body = prepare_markdown_content(raw_content)

        if not markdown_body:
            print("  - LOG: No content left after preparation. Skipping.")
            return 0

        # 2. Split the markdown text into chunks with overlap
        print(f"  - LOG: Splitting text into chunks (Size: {CHUNK_SIZE}, Overlap: {CHUNK_OVERLAP})...")
        chunks = []
        start = 0
        while start < len(markdown_body):
            end = start + CHUNK_SIZE
            chunk_content = markdown_body[start:end]
            chunks.append(chunk_content)
            start += CHUNK_SIZE - CHUNK_OVERLAP
            if start >= len(markdown_body):
                break

        # 3. Prepare document objects for RavenDB
        base_title = md_file.stem.replace("_", " ").replace("-", " ").title()
        documents_to_store = []
        for i, chunk_text in enumerate(chunks):
            chunk_num = i + 1
            # Create an instance of our ContextChunk class
            document_obj = ContextChunk(
                title=f"{base_title} - Chunk {chunk_num}",
                content=chunk_text,
                source_file=md_file.name,
                chunk_number=chunk_num
            )
            documents_to_store.append(document_obj)
        
        print(f"  - LOG: Created {len(documents_to_store)} markdown chunks.")

        # 4. Store all document objects in a single session
        if documents_to_store:
            print("  - LOG: Storing document chunks in RavenDB...")
            with store.open_session() as session:
                for doc_obj in documents_to_store:
                    # Store the object. RavenDB will serialize it correctly.
                    session.store(doc_obj)
                    # Explicitly set the collection for the stored object
                    session.advanced.get_metadata_for(doc_obj)["@collection"] = COLLECTION_NAME
                session.save_changes()
            print("  - ✅ LOG: Documents committed successfully.")
        
        return len(documents_to_store)

    except Exception as e:
        print(f"\n  {'!'*10} CRITICAL FAILURE {'!'*10}")
        print(f"  - ❌ FAILED to process file '{md_file.name}'.")
        print(f"  - Error Type: {type(e).__name__}")
        print(f"  - Error Details: {e}")
        return 0

def main():
    """
    Main function to connect to RavenDB and orchestrate the processing of files.
    """
    print(f"Connecting to RavenDB at {RAVEN_URL}, Database: {DATABASE_NAME}")
    try:
        store = DocumentStore(urls=[RAVEN_URL], database=DATABASE_NAME)
        store.initialize()
        print("✅ Connection successful.")
    except Exception as e:
        print(f"❌ Could not connect to RavenDB. Please check your connection settings. Error: {e}")
        return

    total_chunks_uploaded = 0
    files_processed = 0

    print(f"\nScanning for markdown files in: {MARKDOWN_DIR}")
    if not MARKDOWN_DIR.exists() or not MARKDOWN_DIR.is_dir():
        print(f"❌ ERROR: Directory not found: {MARKDOWN_DIR}")
        return

    markdown_files = list(MARKDOWN_DIR.glob("*.md")) + list(MARKDOWN_DIR.glob("*.markdown"))
    
    if not markdown_files:
        print("  - LOG: No markdown files found in the specified directory.")
        return
    
    print(f"Found {len(markdown_files)} markdown file(s) to process.")

    for md_file in markdown_files:
        files_processed += 1
        chunks_from_file = process_and_chunk_file(store, md_file)
        total_chunks_uploaded += chunks_from_file

    print("\n\n--- All Files Processed ---")
    print(f"Processed {files_processed} file(s).")
    print(f"✅ Successfully uploaded a total of {total_chunks_uploaded} chunk(s) to the '{COLLECTION_NAME}' collection.")

if __name__ == "__main__":
    main()
