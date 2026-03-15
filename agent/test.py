from tools import fetch_knowledge, tavily_search
from pathlib import Path
# print(f'ans: {tavily_search.func("下一届奥运会什么时候？")}')
# EXAMPLE_DIR = Path(__file__).parent
# print(EXAMPLE_DIR)

from chroma_tools.query_chroma import retrieve_by_question
for row in retrieve_by_question("什么是‘认知革命’？它对智人有什么决定性意义"):
    print(row)
