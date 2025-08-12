from flask import Flask, request, jsonify
import os
import groq
from flask_cors import CORS
from dotenv import load_dotenv
import chromadb
from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List, Dict
import uuid
import re
import time
from threading import Lock

# Load environment variables
load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
client = groq.Groq(api_key=groq_api_key)

app = Flask(__name__)
CORS(app)

# In-memory conversation store
conversations = {}
conversations_lock = Lock()

class ConversationManager:
    """Manage conversation state including user names"""
    
    def __init__(self):
        self.conversations = {}
        self.lock = Lock()
        
    def get_or_create_conversation(self, conversation_id: str) -> Dict:
        """Get or create a conversation"""
        with self.lock:
            if conversation_id not in self.conversations:
                self.conversations[conversation_id] = {
                    'user_name': None,
                    'created_at': time.time(),
                    'last_activity': time.time()
                }
            else:
                self.conversations[conversation_id]['last_activity'] = time.time()
            return self.conversations[conversation_id]
    
    def set_user_name(self, conversation_id: str, name: str):
        """Set user name for a conversation"""
        with self.lock:
            if conversation_id in self.conversations:
                self.conversations[conversation_id]['user_name'] = name
                self.conversations[conversation_id]['last_activity'] = time.time()
    
    def get_user_name(self, conversation_id: str) -> str:
        """Get user name for a conversation"""
        with self.lock:
            conv = self.conversations.get(conversation_id)
            return conv['user_name'] if conv else None
    
    def cleanup_old_conversations(self, max_age_hours: int = 24):
        """Clean up conversations older than max_age_hours"""
        with self.lock:
            current_time = time.time()
            to_remove = []
            for conv_id, conv_data in self.conversations.items():
                if current_time - conv_data['last_activity'] > max_age_hours * 3600:
                    to_remove.append(conv_id)
            
            for conv_id in to_remove:
                del self.conversations[conv_id]

# Initialize SentenceTransformer
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

# VREG Knowledge Base
vreg_faqs = [
    {
        "question": "I am not getting my confirmation link after registration",
        "answer": "Check your spam folder or confirm if you used the correct email address in creating a VREG account.",
        "category": "registration"
    },
    {
        "question": "After inputting my TIN on the portal, it brought out an Invalid statement",
        "answer": "Go to www.trade.gov.ng, click on Agencies then FIRS to validate your TIN",
        "category": "registration"
    },
    {
        "question": "I cannot access my dashboard because I forgot the password",
        "answer": "Go to www.vreg.gov.ng, click on login, then forget password, enter the email address used during registration and click on recover. A link will be sent to your email, click on the link and create a new password.",
        "category": "registration"
    },
    {
        "question": "I tried to register for VREG but after inputting my TIN/Agency code it says TIN/Agency code has been taken",
        "answer": "Your agency already has an account created with VREG, kindly confirm the email address used during the registration and reset your password to be able to login successfully.",
        "category": "registration"
    },
    {
        "question": "The portal is not recognizing my VIN, showing 'Warning! This is a non-standard VIN'",
        "answer": "This is a non-standard VIN that needs manual validation. Input the HS code and VIN number, then click submit. A prompt will appear, click try again, submit the VIN and HS code, click next and input the vehicle information and submit while you await VIN validation.",
        "category": "vin_validation"
    },
    {
        "question": "I submitted my VIN for validation and it's showing on the Pending tab",
        "answer": "Your VIN has been validated manually, kindly generate an invoice and proceed to make payment.",
        "category": "vin_validation"
    },
    {
        "question": "I submitted a wrong VIN for manual validation, how can I cancel it?",
        "answer": "The VIN will automatically be erased from your dashboard once the due date elapses.",
        "category": "vin_validation"
    },
    {
        "question": "SGD Portal is telling me VREG does not exist",
        "answer": "Take a screenshot of the error message and attach the affected VREG certificate and send it to support@vreg.gov.ng",
        "category": "transmission"
    },
    {
        "question": "My VREG certificate information was not transmitted to customs ESGD platform",
        "answer": "This is a transmission case where VREG certificate information failed to reach customs. Please contact support@vreg.gov.ng with your certificate details and error screenshots.",
        "category": "transmission"
    },
    {
        "question": "I made a payment for a VREG certificate but no certificate was generated",
        "answer": "Send the invoice number, payment proof and the date payment were made to payments@vreg.gov.ng",
        "category": "payment"
    },
    {
        "question": "How can I get access to the certificate which I generated on the VREG portal?",
        "answer": "Login to your dashboard, click on certificate and then enter either the invoice number or VIN for the vehicle on the search tab to be able to view the certificate.",
        "category": "payment"
    },
    {
        "question": "My VIN is generating multiple invoices. Can I make the payment?",
        "answer": "Select a single Invoice number and proceed to initiate payment for the VIN.",
        "category": "payment"
    },
    {
        "question": "My payment is under investigation",
        "answer": "Kindly note that this issue is under investigation. Once the payment has been confirmed successful, the VREG certificate will be generated. Endeavor to check your payment status occasionally.",
        "category": "payment"
    },
    {
        "question": "My payment transaction was unsuccessful",
        "answer": "Kindly note that your payment transaction was unsuccessful on this invoice. Reach out or contact your bank to log a complaint or seek a reversal.",
        "category": "payment"
    },
    {
        "question": "How do I request a refund?",
        "answer": "Kindly fill in your details to process your refund: Full Name, Email Address, Phone Number, Excess Amount Paid, Invoice Number, Proof of Payment, Account Number, Account Name, Transaction Date, Bank Name. Your refund will be processed within 3-7 working days.",
        "category": "payment"
    },
    {
        "question": "The Agency we used for capturing has been blocked. I want to change to another",
        "answer": "The consignee should write a letter of cancellation of VREG certificate addressing it to the managing director of VREG, and attach the bill of lading, VREG certificate and a CAC certificate (if consignee is a company) or a Valid means of identification (if consignee is an individual) and send it to support@vreg.gov.ng",
        "category": "agency"
    },
    {
        "question": "A wrong consignee TIN was used in generating a VREG certificate",
        "answer": "The agency should write a letter of cancellation of the VREG certificate addressing it to the managing director of VREG, and attach the bill of lading and VREG certificate.",
        "category": "agency"
    },
    {
        "question": "I cannot access the Vehicle on Custom portal because it says the company's code on VREG is different from that on ESGD",
        "answer": "Kindly enter the correct consignee's TIN that was used in generating the VREG certificate to be able to access the Vehicle on customs portal.",
        "category": "agency"
    },
    {
        "question": "After entering my correct login details an error message pop up saying 'this field is required'",
        "answer": "Ensure you have a good network connection and then try to login again.",
        "category": "technical"
    },
    {
        "question": "What is VREG?",
        "answer": "The National Vehicle Registry (VREG) is the centralized database for all vehicles in Nigeria through unique Vehicle Identification Numbers (VIN). It stores detailed vehicular information such as specifications, ownership, and history of each vehicle in Nigeria.",
        "category": "general"
    },
    {
        "question": "What is the purpose of VREG?",
        "answer": "VREG was created by the Federal Ministry of Finance as a solution to customs duty evasion, vehicle theft, vehicle-related crimes, and ineffective vehicle insurance coverage. All vehicle owners are required to register their vehicles using the VIN on the VREG portal.",
        "category": "general"
    },
    {
        "question": "What documents do I need for VREG registration?",
        "answer": "You'll need: Valid ID/Passport, Vehicle purchase receipt, Customs clearance certificate, Insurance certificate, Vehicle inspection report, and your Tax Identification Number (TIN).",
        "category": "general"
    },
    {
        "question": "How can I contact VREG support?",
        "answer": "You can contact VREG support via: Email: support@vreg.gov.ng, Payment issues: payments@vreg.gov.ng, Phone: Contact helpdesk, Visit: Physical walk-in support, Website: www.vreg.gov.ng",
        "category": "general"
    }
]

class HyperlinkProcessor:
    """Class to handle hyperlink processing for VREG responses"""
    
    @staticmethod
    def convert_to_hyperlinks(text: str) -> str:
        """Convert URLs and email addresses to HTML hyperlinks"""
        # Email regex pattern
        email_pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
        
        # URL regex pattern (matches www.domain.com and full URLs)
        url_pattern = r'((?:https?://)?(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)'
        
        # First convert URLs (avoiding emails)
        def url_replacer(match):
            url = match.group(1)
            # Skip if it's an email address
            if '@' in url:
                return url
            
            # Handle specific domain mappings
            href = url
            if not url.startswith('http'):
                if 'www.vreg.gov.ng' in url:
                    href = url.replace('www.vreg.gov.ng', 'https://vreg.gov.ng')
                elif 'www.trade.gov.ng' in url:
                    href = url.replace('www.trade.gov.ng', 'https://trade.gov.ng')
                elif url.startswith('www.'):
                    href = f'https://{url[4:]}'  # Remove 'www.' and add https://
                else:
                    href = f'https://{url}'
            
            return f'<a href="{href}" target="_blank" rel="noopener noreferrer" style="color: #0066cc; text-decoration: underline; font-weight: 500;">{url}</a>'
        
        # Apply URL conversion
        result = re.sub(url_pattern, url_replacer, text)
        
        # Then convert email addresses to mailto links
        def email_replacer(match):
            email = match.group(1)
            return f'<a href="mailto:{email}" style="color: #0066cc; text-decoration: underline; font-weight: 500;">{email}</a>'
        
        # Apply email conversion
        result = re.sub(email_pattern, email_replacer, result)
        
        return result
    
    @staticmethod
    def process_faq_answer(answer: str) -> str:
        """Process FAQ answer to include hyperlinks"""
        return HyperlinkProcessor.convert_to_hyperlinks(answer)

class VREGRAGSystem:
    def __init__(self):
        self.collection_name = "vreg_faqs"
        # Initialize ChromaDB client as instance attribute
        self.chroma_client = chromadb.Client()
        self.hyperlink_processor = HyperlinkProcessor()
        self.setup_vector_database()
    
    def setup_vector_database(self):
        """Initialize ChromaDB collection with VREG FAQs"""
        try:
            # Delete existing collection if it exists
            try:
                self.chroma_client.delete_collection(name=self.collection_name)
            except:
                pass
            
            # Create new collection
            self.collection = self.chroma_client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            
            # Prepare documents for embedding
            documents = []
            metadatas = []
            ids = []
            
            for i, faq in enumerate(vreg_faqs):
                # Combine question and answer for better context
                doc_text = f"Question: {faq['question']}\nAnswer: {faq['answer']}"
                documents.append(doc_text)
                metadatas.append({
                    "category": faq['category'],
                    "question": faq['question'],
                    "answer": faq['answer']
                })
                ids.append(str(uuid.uuid4()))
            
            # Add documents to collection
            self.collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            
            print(f"✅ Vector database initialized with {len(vreg_faqs)} FAQs")
            
        except Exception as e:
            print(f"❌ Error setting up vector database: {e}")
    
    def retrieve_relevant_faqs(self, query: str, n_results: int = 3) -> List[Dict]:
        """Retrieve most relevant FAQs based on user query"""
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                include=['documents', 'metadatas', 'distances']
            )
            
            relevant_faqs = []
            if results['documents'] and results['documents'][0]:
                for i, doc in enumerate(results['documents'][0]):
                    metadata = results['metadatas'][0][i]
                    distance = results['distances'][0][i]
                    
                    # Process answer with hyperlinks
                    processed_answer = self.hyperlink_processor.process_faq_answer(metadata['answer'])
                    
                    relevant_faqs.append({
                        'question': metadata['question'],
                        'answer': metadata['answer'],  # Keep original for context
                        'answer_with_links': processed_answer,  # Processed version with links
                        'category': metadata['category'],
                        'relevance_score': 1 - distance  # Convert distance to similarity score
                    })
            
            return relevant_faqs
            
        except Exception as e:
            print(f"❌ Error retrieving FAQs: {e}")
            return []
    
    def generate_rag_response(self, user_query: str, user_name: str = None) -> Dict:
        """Generate response using RAG approach with hyperlink processing"""
        try:
            # Step 1: Retrieve relevant FAQs
            relevant_faqs = self.retrieve_relevant_faqs(user_query, n_results=3)
            
            # Step 2: Prepare context from retrieved FAQs
            context = ""
            if relevant_faqs:
                context = "Here are some relevant FAQs from the VREG knowledge base:\n\n"
                for i, faq in enumerate(relevant_faqs, 1):
                    context += f"{i}. Q: {faq['question']}\n   A: {faq['answer']}\n\n"
            
            # Step 3: Create enhanced system prompt with name context
            name_context = f"The user's name is {user_name}. Use their name naturally in your responses when appropriate." if user_name else ""
            
            system_prompt = f"""You are a helpful AI assistant for the VREG (National Vehicle Registry) platform in Nigeria. 

{name_context}

Your role is to help users with vehicle registration, VIN validation, payment issues, customs clearance, and other VREG-related queries.

INSTRUCTIONS:
1. Use the provided knowledge base to answer questions when relevant
2. If the user's question is covered in your knowledge base, use that information as your primary source
3. Be helpful, professional, and specific in your responses
4. If you don't have specific information, guide users to contact support@vreg.gov.ng or payments@vreg.gov.ng for payment issues
5. Always maintain a helpful and courteous tone
6. Provide step-by-step instructions when applicable
7. When mentioning websites or email addresses, use the exact format provided (e.g., www.vreg.gov.ng, support@vreg.gov.ng)
8. Continue conversations naturally - do not ask for the user's name again if you already know it
9. Use names ONLY in the initial greeting. After that, avoid using names entirely unless the conversation has been going on for a very long time and you want to add a personal touch
10. Avoid using their name in every response as it sounds robotic
11. When referencing your knowledge base, use natural phrases like "based on the information available to me," "from what I can see," or "according to our system" - never mention FAQs, documentation, or knowledge base directly
12. Keep responses concise but warm - aim for 2-3 short paragraphs maximum
13. Use friendly, conversational language with phrases like "I'd be happy to help" and "Let me know if..."
14. Show empathy when users have problems ("I'm sorry to hear..." "Let me help you sort this out")
15. Ask follow-up questions in a caring way to gather specific details
16. After the first greeting, use warm transitions like "I'd be happy to help with..." instead of formal re-introductions
17. End responses with supportive offers like "I'm here to help" or "Let me know if you need anything else"
18. Avoid repetitive greetings - after the first interaction, jump straight to helping
19. When users say "thank you," "thanks," or are clearly ending the conversation, keep responses brief and natural - just acknowledge their thanks and offer future help in 1-2 sentences maximum
20. Avoid adding website links, contact information, or promotional text when users are simply expressing gratitude or saying goodbye
21. For thank you/goodbye responses, use simple phrases like: "You're very welcome!" "Happy to help!" "Take care!" followed by only "Feel free to reach out if you need anything else"
22. Do not repeat contact information or website details unless the user specifically asks for it

IMPORTANT CONTACT INFORMATION:
- General support: support@vreg.gov.ng
- Payment issues: payments@vreg.gov.ng
- Website: www.vreg.gov.ng
- TIN validation: www.trade.gov.ng (Agencies > FIRS)"""
            
            # Step 4: Create user prompt with context
            if context:
                user_prompt = f"{context}\n\nUser Question: {user_query}\n\nPlease provide a helpful response based on the FAQ context above and your knowledge of VREG processes."
            else:
                user_prompt = f"User Question: {user_query}\n\nPlease provide a helpful response about VREG processes."
            
            # Step 5: Generate response using Groq
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model="llama-3.1-8b-instant",
                temperature=0.1,  # Lower temperature for more consistent responses
                max_tokens=800,
                top_p =0.9,
                frequency_penalty=0.1
            )
            
            raw_response = chat_completion.choices[0].message.content
            
            # Step 6: Process response to add hyperlinks
            processed_response = self.hyperlink_processor.convert_to_hyperlinks(raw_response)
            
            # Step 7: Return both versions
            return {
                "response": raw_response,
                "response_with_links": processed_response,
                "relevant_faqs": relevant_faqs,
                "context_used": bool(context)
            }
            
        except Exception as e:
            print(f"❌ Error generating RAG response: {e}")
            error_message = "I apologize, but I'm having trouble processing your request right now. Please contact support@vreg.gov.ng for assistance."
            return {
                "response": error_message,
                "response_with_links": self.hyperlink_processor.convert_to_hyperlinks(error_message),
                "relevant_faqs": [],
                "context_used": False
            }

# Initialize RAG system
rag_system = VREGRAGSystem()

# Initialize conversation manager
conversation_manager = ConversationManager()

def extract_name_from_message(message: str) -> str:
    """Extract name from user message"""
    message_lower = message.lower().strip()
    
    # Common patterns for name introduction - ONLY explicit name patterns
    name_patterns = [
        r"my name is\s+(\w+)",
        r"i'm\s+(\w+)",
        r"i am\s+(\w+)",
        r"call me\s+(\w+)",
        r"it's\s+(\w+)",
        r"this is\s+(\w+)",
        r"name:\s*(\w+)",
        r"^(\w+)$"  # Single word ONLY if it looks like a proper name
    ]
    
    # Expanded list of common non-names to avoid
    non_names = [
        'hi', 'hello', 'hey', 'good', 'morning', 'afternoon', 'evening',
        'yes', 'no', 'ok', 'okay', 'sure', 'please', 'help', 'thanks', 'thank',
        'what', 'how', 'when', 'where', 'why', 'who', 'which',
        'vreg', 'registration', 'vehicle', 'portal', 'login', 'password',
        'payment', 'certificate', 'support', 'problem', 'issue', 'error',
        'can', 'will', 'should', 'could', 'would', 'need', 'want', 'like',
        'get', 'have', 'make', 'take', 'give', 'find', 'know', 'think',
        'see', 'look', 'check', 'try', 'use', 'work', 'go', 'come'
    ]
    
    for pattern in name_patterns:
        match = re.search(pattern, message_lower)
        if match:
            potential_name = match.group(1).strip()
            
            # For single word pattern, be more strict
            if pattern == r"^(\w+)$":
                # Must be at least 2 characters, start with capital when original, and not in non-names
                original_word = message.strip()
                if (len(potential_name) >= 2 and 
                    potential_name.lower() not in non_names and 
                    original_word[0].isupper() and  # Original message starts with capital
                    original_word.isalpha()):  # Contains only letters
                    return potential_name.capitalize()
            else:
                # For explicit patterns like "my name is", be less strict
                if (len(potential_name) >= 2 and 
                    potential_name.lower() not in non_names):
                    return potential_name.capitalize()
    
    return None

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message")
    conversation_id = request.json.get("conversation_id", "default")  # Get conversation ID from request
    
    if not user_input:
        return jsonify({"error": "No message received"}), 400
    
    try:
        # Get or create conversation
        conversation = conversation_manager.get_or_create_conversation(conversation_id)
        user_name = conversation.get('user_name')
        
        # If no name in conversation, first check if this is a name response
        if not user_name:
            extracted_name = extract_name_from_message(user_input)
            if extracted_name:
                conversation_manager.set_user_name(conversation_id, extracted_name)
                user_name = extracted_name
                # Acknowledge the name and ask how to help
                response = f"Hello {user_name}! It's nice to meet you. How can I assist you today with the VREG platform? Do you have any questions, need help with vehicle registration, or perhaps you're experiencing some issues that you'd like me to help resolve?"
                processed_response = rag_system.hyperlink_processor.convert_to_hyperlinks(response)
                return jsonify({
                    "reply": processed_response,
                    "raw_reply": response,
                    "relevant_faqs": [],
                    "context_used": False,
                    "name_captured": True,
                    "conversation_id": conversation_id
                })
            else:
                # Ask for name if not provided and not in conversation
                # Don't treat greetings as requests for help
                greeting_words = ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']
                if any(greeting in user_input.lower() for greeting in greeting_words):
                    return jsonify({
                        "reply": "Hello! May I know your name?",
                        "raw_reply": "Hello! May I know your name?",
                        "relevant_faqs": [],
                        "context_used": False,
                        "asking_for_name": True,
                        "conversation_id": conversation_id
                    })
                else:
                    return jsonify({
                        "reply": "May I know your name?",
                        "raw_reply": "May I know your name?",
                        "relevant_faqs": [],
                        "context_used": False,
                        "asking_for_name": True,
                        "conversation_id": conversation_id
                    })
        
        # Generate response using RAG with user name
        response_data = rag_system.generate_rag_response(user_input, user_name)
        
        return jsonify({
            "reply": response_data["response_with_links"],  # Send processed response with links
            "raw_reply": response_data["response"],  # Also include raw response
            "relevant_faqs": response_data["relevant_faqs"],
            "context_used": response_data["context_used"],
            "user_name": user_name,
            "conversation_id": conversation_id
        })
    
    except Exception as e:
        print(f"❌ Error in chat endpoint: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/reset-session", methods=["POST"])
def reset_session():
    """Reset user session (clear name)"""
    session.clear()
    return jsonify({"message": "Session reset successfully"})

@app.route("/get-session", methods=["GET"])
def get_session():
    """Get current session info"""
    return jsonify({
        "user_name": session.get('user_name'),
        "has_name": bool(session.get('user_name'))
    })

@app.route("/search", methods=["POST"])
def search_faqs():
    """Endpoint to search FAQs directly"""
    query = request.json.get("query")
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    try:
        relevant_faqs = rag_system.retrieve_relevant_faqs(query, n_results=5)
        return jsonify({"faqs": relevant_faqs})
    
    except Exception as e:
        print(f"❌ Error in search endpoint: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "rag_system": "operational",
        "total_faqs": len(vreg_faqs),
        "hyperlink_processing": "enabled",
        "session_support": "enabled"
    })

@app.route("/process-text", methods=["POST"])
def process_text():
    """Endpoint to process any text and add hyperlinks"""
    text = request.json.get("text")
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    try:
        processed_text = rag_system.hyperlink_processor.convert_to_hyperlinks(text)
        return jsonify({
            "original_text": text,
            "processed_text": processed_text
        })
    
    except Exception as e:
        print(f"❌ Error in process-text endpoint: {e}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    app.run(debug=True)