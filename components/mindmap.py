from __future__ import annotations
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import re
from typing import Optional, Tuple, List, Union, Literal
import base64
import matplotlib.pyplot as plt
import streamlit as st
from streamlit.delta_generator import DeltaGenerator
import os
from mistralai import Mistral
from dataclasses import dataclass, asdict
from textwrap import dedent
from streamlit_agraph import agraph, Node, Edge, Config
from prompts.system_prompts import (
    MINDMAP_SYSTEM_PROMPT,
    MINDMAP_INSTRUCTION_PROMPT,
    MINDMAP_EXAMPLE_CONVERSATION
)

st.set_page_config(page_title="AI Mind Maps", layout="wide")

# Update the color constants with better values
COLOR = "#00CED1"
FOCUS_COLOR = "#FF4500"

mistral_client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))

@dataclass
class Message:
    """A class that represents a message in a Mistral conversation.
    """
    content: str
    role: Literal["user", "system", "assistant"]

    # is a built-in method for dataclasses
    # called after the __init__ method
    def __post_init__(self):
        self.content = dedent(self.content).strip()

START_CONVERSATION = [
    Message(MINDMAP_SYSTEM_PROMPT, role="system"),
    Message(MINDMAP_INSTRUCTION_PROMPT, role="user"),
]

# Add example conversation messages
for msg in MINDMAP_EXAMPLE_CONVERSATION:
    START_CONVERSATION.append(Message(msg["content"], role=msg["role"]))

def ask_mistral(conversation: List[Message]) -> Tuple[str, List[Message]]:
    """Ask Mistral to generate or modify the mind map.
    
    Args:
        conversation (List[Message]): The conversation history.
        
    Returns:
        Tuple[str, List[Message]]: The response text and updated conversation.
    """
    response = mistral_client.chat.complete(
        model="mistral-large-latest",
        messages=[asdict(c) for c in conversation]
    )
    msg = Message(
        content=response.choices[0].message.content,
        role="assistant"
    )
    return msg.content, conversation + [msg]

class MindMap:
    """A class that represents a mind map as a graph.
    """
    
    def __init__(self, edges: Optional[List[Tuple[str, str]]]=None, nodes: Optional[List[str]]=None) -> None:
        self.edges = [] if edges is None else edges
        self.nodes = [] if nodes is None else nodes
        self.save()

    @classmethod
    def load(cls) -> MindMap:
        """Load mindmap from session state if it exists
        
        Returns: Mindmap
        """
        if "mindmap" in st.session_state:
            return st.session_state["mindmap"]
        return cls()

    def save(self) -> None:
        # save to session state
        st.session_state["mindmap"] = self

    def is_empty(self) -> bool:
        return len(self.edges) == 0
    
    def ask_for_initial_graph(self, query: str) -> None:
        """Ask Mistral to construct a graph from scratch.

        Args:
            query (str): The query to ask Mistral about.
        """
        conversation = START_CONVERSATION + [
            Message(f"""
                Great, now ignore all previous nodes and restart from scratch. I now want you do the following:    

                {query}
            """, role="user")
        ]

        output, self.conversation = ask_mistral(conversation)
        self.parse_and_include_edges(output, replace=True)

    def ask_for_extended_graph(self, selected_node: Optional[str]=None, text: Optional[str]=None) -> None:
        """Ask Mistral to extend the graph.

        Args:
            selected_node (Optional[str]): Node to expand from
            text (Optional[str]): Text description of how to modify the graph
        """
        if (selected_node is None and text is None):
            return

        if selected_node is not None:
            conversation = self.conversation + [
                Message(f"""
                    add new edges to new nodes, starting from the node "{selected_node}"
                """, role="user")
            ]
            st.session_state.last_expanded = selected_node
        else:
            conversation = self.conversation + [Message(text, role="user")]

        output, self.conversation = ask_mistral(conversation)
        self.parse_and_include_edges(output, replace=False)

    def parse_and_include_edges(self, output: str, replace: bool=True) -> None:
        """Parse output from Mistral and include the edges in the graph.

        Args:
            output (str): output from Mistral to be parsed
            replace (bool, optional): if True, replace all edges with the new ones, 
                otherwise add to existing edges. Defaults to True.
        """

        # Regex patterns
        pattern1 = r'(add|delete)\("([^()"]+)",\s*"([^()"]+)"\)'
        pattern2 = r'(delete)\("([^()"]+)"\)'

        # Find all matches in the text
        matches = re.findall(pattern1, output) + re.findall(pattern2, output)

        new_edges = []
        remove_edges = set()
        remove_nodes = set()
        for match in matches:
            op, *args = match
            add = op == "add"
            if add or (op == "delete" and len(args)==2):
                a, b = args
                if a == b:
                    continue
                if add:
                    new_edges.append((a, b))
                else:
                    # remove both directions
                    # (undirected graph)
                    remove_edges.add(frozenset([a, b]))
            else: # must be delete of node
                remove_nodes.add(args[0])

        if replace:
            edges = new_edges
        else:
            edges = self.edges + new_edges

        # make sure edges aren't added twice
        # and remove nodes/edges that were deleted
        added = set()
        for edge in edges:
            nodes = frozenset(edge)
            if nodes in added or nodes & remove_nodes or nodes in remove_edges:
                continue
            added.add(nodes)

        self.edges = list([tuple(a) for a in added])
        self.nodes = list(set([n for e in self.edges for n in e]))
        self.save()

    def _delete_node(self, node) -> None:
        """Delete a node and all edges connected to it.

        Args:
            node (str): The node to delete.
        """
        self.edges = [e for e in self.edges if node not in frozenset(e)]
        self.nodes = list(set([n for e in self.edges for n in e]))
        self.conversation.append(Message(
            f'delete("{node}")', 
            role="user"
        ))
        self.save()

    def _add_expand_delete_buttons(self, node) -> None:
        """Add expand and delete buttons for a specific node in the sidebar.
        Only called when a node is clicked.

        Args:
            node (str): The node to add buttons for
        """
        st.sidebar.subheader(node)
        cols = st.sidebar.columns(2)
        cols[0].button(
            label="Expand", 
            on_click=self.ask_for_extended_graph,
            key=f"expand_{node}",
            kwargs={"selected_node": node}
        )
        cols[1].button(
            label="Delete", 
            on_click=self._delete_node,
            type="primary",
            key=f"delete_{node}",
            args=(node,)
        )

    def visualize(self) -> None:
        """Visualize the mindmap as an interactive graph using agraph."""
        try:
            selected = st.session_state.get("last_expanded")
            vis_nodes = [
                Node(
                    id=n, 
                    label=n, 
                    size=10+10*(n==selected), 
                    color=COLOR if n != selected else FOCUS_COLOR
                ) 
                for n in self.nodes
            ]
            vis_edges = [Edge(source=a, target=b) for a, b in self.edges]
            config = Config(width="100%",
                          height=600,
                          directed=False, 
                          physics=True,
                          hierarchical=False,
                          )
            clicked_node = agraph(nodes=vis_nodes, 
                            edges=vis_edges, 
                            config=config)
            
            # Only show buttons for clicked node
            if clicked_node:
                self._add_expand_delete_buttons(clicked_node)
            
        except Exception as e:
            st.error(f"Visualization error: {str(e)}")

def main():
    # will initialize the graph from session state
    # (if it exists) otherwise will create a new one
    mindmap = MindMap.load()

    st.sidebar.title("AI Mind Map Generator")
    
    empty = mindmap.is_empty()
    query = st.sidebar.text_area(
        "Enter your prompt to generate a mind map", 
        value=st.session_state.get("mindmap-input", ""),
        key="mindmap-input",
        height=200
    )
    submit = st.sidebar.button("Submit")

    valid_submission = submit and query != ""

    if empty and not valid_submission:
        return

    with st.spinner(text="Loading graph..."):
        # if submit and non-empty query, then create new mindmap
        if valid_submission:
            # Always create a new mindmap when submitting a prompt
            mindmap.ask_for_initial_graph(query=query)
            st.experimental_rerun()
        else:
            mindmap.visualize()

if __name__ == "__main__":
    main()