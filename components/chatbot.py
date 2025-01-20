import streamlit as st
import os
from mistralai import Mistral
from dotenv import load_dotenv
from prompts.system_prompts import DEFAULT_ASSISTANT_PROMPT, VISUALIZATION_EXPERT_PROMPT
from utils.code_interpreter import CodeInterpreter
from typing import Optional
from datetime import datetime
from services.snowflake_utils import SnowflakeConnector
from config import SnowflakeConfig
from components.mindmap import MindMap

def init_chat_history():
    """Initialize chat history in session state"""
    if "chats" not in st.session_state:
        st.session_state.chats = {}
    
    if "current_chat_id" not in st.session_state:
        new_chat_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.session_state.current_chat_id = new_chat_id
        st.session_state.chats[new_chat_id] = {
            "title": "New Chat",
            "messages": [
                {
                    "role": "assistant",
                    "content": "Hi! I'm a chatbot powered by Mistral AI. I can help you analyze documents, answer questions, and assist with various tasks. How can I help you today? 🤖"
                }
            ]
        }

def get_current_chat():
    """Get the current chat's messages"""
    if st.session_state.current_chat_id in st.session_state.chats:
        return st.session_state.chats[st.session_state.current_chat_id]["messages"]
    return []

def add_message(role, content):
    """Add a message to the current chat"""
    if st.session_state.current_chat_id in st.session_state.chats:
        st.session_state.chats[st.session_state.current_chat_id]["messages"].append({
            "role": role,
            "content": content
        })
        # Update chat title based on first user message if it's still "New Chat"
        if (role == "user" and 
            len(st.session_state.chats[st.session_state.current_chat_id]["messages"]) == 2 and
            st.session_state.chats[st.session_state.current_chat_id]["title"] == "New Chat"):
            st.session_state.chats[st.session_state.current_chat_id]["title"] = content[:30] + "..."

def start_new_chat():
    """Start a new chat session"""
    new_chat_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.session_state.current_chat_id = new_chat_id
    st.session_state.chats[new_chat_id] = {
        "title": "New Chat",
        "messages": [
            {
                "role": "assistant",
                "content": "Hi! I'm a chatbot powered by Mistral AI. I can help you analyze documents, answer questions, and assist with various tasks. How can I help you today? 🤖"
            }
        ]
    }
    st.experimental_rerun()

def render_chatbot():
    st.markdown('<div class="chat-container">', unsafe_allow_html=True)
    
    # Load environment variables
    load_dotenv()
    
    # Get API key from environment
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        st.error("Please set your Mistral API key in the .env file")
        return
    
    init_chat_history()
    # Add title with custom class
    st.markdown('<h1 class="chat-title">💬 Chat with Lexis</h1>', unsafe_allow_html=True)

    # Display current chat messages
    messages = get_current_chat()
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Add RAG status indicator
    # if hasattr(st.session_state, 'chatbot') and st.session_state.chatbot.snowflake:
    #     st.sidebar.success("📚 Knowledge Base: Connected")
    # else:
    #     st.sidebar.warning("📚 Knowledge Base: Disconnected")
    
    # Chat input
    if prompt := st.chat_input("Message Mistral..."):
        # Add user message to chat
        with st.chat_message("user"):
            st.markdown(prompt)
        add_message("user", prompt)
        
        # Get bot response
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            try:
                chatbot = Chatbot()
                
                # Check if it's a visualization request
                if any(keyword in prompt.lower() for keyword in ['histogram', 'plot', 'graph', 'visualize', 'chart']):
                    response = chatbot.process_visualization_request(prompt)
                else:
                    # Handle regular chat responses
                    response = chatbot.process_query(prompt)
                
                message_placeholder.markdown(response)
                add_message("assistant", response)
                
            except Exception as e:
                message_placeholder.error(f"Error: {str(e)}")

class Chatbot:
    def __init__(self):
        self.code_interpreter = CodeInterpreter()
        # Initialize Mistral client
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("Please set your Mistral API key in the .env file")
        self.mistral_client = Mistral(api_key=api_key)
        
        # Initialize Snowflake RAG
        try:
            snowflake_config = SnowflakeConfig()
            self.snowflake = SnowflakeConnector(snowflake_config)
        except Exception as e:
            st.error(f"Error initializing Snowflake: {str(e)}")
            self.snowflake = None

    def is_mindmap_request(self, query: str) -> bool:
        """Check if the query is requesting a mindmap"""
        mindmap_keywords = [
            'mind map', 'mindmap', 'knowledge graph', 'knowledgegraph',
            'mindmaps', 'knowledge graphs', 'knowledgegraphs'
        ]
        return any(keyword in query.lower() for keyword in mindmap_keywords)

    def process_mindmap_request(self, query: str) -> str:
        """Handle mindmap generation requests"""
        try:
            # Initialize or get existing mindmap
            mindmap = MindMap.load()
            
            # Generate new mindmap
            mindmap.ask_for_initial_graph(query=query)
            
            # Store mindmap in session state for info panel
            st.session_state.show_mindmap = True
            st.session_state.current_mindmap = mindmap
            
            return "I've created a mind map based on your request. You can view and interact with it in the information panel. Click on nodes to expand or delete them!"
            
        except Exception as e:
            st.error(f"Error creating mind map: {str(e)}")
            return "Sorry, I encountered an error while creating the mind map."

    def process_visualization_request(self, query: str) -> str:
        """Handle visualization requests"""
        try:
            response = self.mistral_client.chat.complete(
                model="mistral-large-latest",
                messages=[
                    {"role": "system", "content": VISUALIZATION_EXPERT_PROMPT},
                    {"role": "user", "content": query}
                ]
            )
            
            # Extract just the Python code from the response
            code = response.choices[0].message.content
            
            # If the response contains markdown code blocks, extract just the code
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0].strip()
            elif "```" in code:
                code = code.split("```")[1].split("```")[0].strip()
            
            # Execute the code
            results = self.code_interpreter.execute_code(code)
            if results:
                self.code_interpreter.display_results(results)
                return "I've created the visualization based on your request. Let me know if you'd like any adjustments!"
            return "I couldn't create the visualization. Please try again with a different request."
            
        except Exception as e:
            st.error(f"Error creating visualization: {str(e)}")
            return "Sorry, I encountered an error while creating the visualization."

    def process_query(self, query: str) -> str:
        """Process user query using RAG if available, otherwise fall back to regular chat"""
        try:
            # Check if it's a mindmap request
            if self.is_mindmap_request(query):
                return self.process_mindmap_request(query)
            # Check if it's a visualization request
            elif any(keyword in query.lower() for keyword in ['histogram', 'plot', 'graph', 'visualize', 'chart']):
                return self.process_visualization_request(query)
            # Handle regular queries
            elif self.snowflake:
                # Get RAG context and prompt
                prompt, source_paths = self.snowflake.create_prompt(query)
                
                # Use Mistral with RAG context
                response = self.mistral_client.chat.complete(
                    model="mistral-large-latest",
                    messages=[
                        {"role": "system", "content": DEFAULT_ASSISTANT_PROMPT},
                        {"role": "user", "content": prompt}
                    ]
                )
                
                # Add source attribution if sources were found
                answer = response.choices[0].message.content
                if source_paths:
                    sources_list = "\n".join([f"- {path}" for path in source_paths])
                    answer += f"\n\nSources:\n{sources_list}"
                
                return answer
            else:
                # Fallback to regular chat if Snowflake is not available
                return self._process_regular_query(query)
                
        except Exception as e:
            st.error(f"Error processing query: {str(e)}")
            return "Sorry, I encountered an error while processing your request."

    def _process_regular_query(self, query: str) -> str:
        """Fallback method for regular chat without RAG"""
        response = self.mistral_client.chat.complete(
            model="mistral-large-latest",
            messages=[
                {"role": "system", "content": DEFAULT_ASSISTANT_PROMPT},
                {"role": "user", "content": query}
            ]
        )
        return response.choices[0].message.content

    def cleanup(self):
        """Cleanup resources"""
        if hasattr(self, 'code_interpreter'):
            self.code_interpreter.cleanup()
        if hasattr(self, 'snowflake'):
            try:
                self.snowflake.session.close()
            except:
                pass

    def __del__(self):
        """Cleanup resources on deletion"""
        self.cleanup()