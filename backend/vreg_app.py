from flask import Flask, request, jsonify
import os
import groq
from flask_cors import CORS
from dotenv import load_dotenv
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
        
    def get_or_create_conversation(self, conversation_id: str) -> dict:
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

# Initialize conversation manager
conversation_manager = ConversationManager()

# VREG Knowledge Base (simplified - no vector search)
vreg_faqs = [
    {
        "question": "I am not getting my confirmation link after registration",
        "answer": "Check your spam folder or confirm if you used the correct email address in creating a VREG account.",
        "keywords": ["confirmation", "link", "registration", "email"]
    },
    {
        "question": "After inputting my TIN on the portal, it brought out an Invalid statement",
        "answer": "Go to www.trade.gov.ng, click on Agencies then FIRS to validate your TIN",
        "keywords": ["TIN", "invalid", "validation"]
    },
    {
        "question": "I cannot access my dashboard because I forgot the password",
        "answer": "Go to www.vreg.gov.ng, click on login, then forget password, enter the email address used during registration and click on recover. A link will be sent to your email, click on the link and create a new password.",
        "keywords": ["password", "forgot", "dashboard", "login"]
    },
    {
        "question": "I tried to register for VREG but after inputting my TIN/Agency code it says TIN/Agency code has been taken",
        "answer": "Your agency already has an account created with VREG, kindly confirm the email address used during the registration and reset your password to be able to login successfully.",
        "keywords": ["TIN", "taken", "agency", "registration"]
    },
    {
        "question": "The portal is not recognizing my VIN, showing 'Warning! This is a non-standard VIN'",
        "answer": "This is a non-standard VIN that needs manual validation. Input the HS code and VIN number, then click submit. A prompt will appear, click try again, submit the VIN and HS code, click next and input the vehicle information and submit while you await VIN validation.",
        "keywords": ["VIN", "non-standard", "validation", "warning"]
    },
    {
        "question": "I made a payment for a VREG certificate but no certificate was generated",
        "answer": "Send the invoice number, payment proof and the date payment were made to payments@vreg.gov.ng",
        "keywords": ["payment", "certificate", "generated", "invoice"]
    },
    {
        "question": "My payment is under investigation",
        "answer": "Kindly note that this issue is under investigation. Once the payment has been confirmed successful, the VREG certificate will be generated. Endeavor to check your payment status occasionally.",
        "keywords": ["payment", "investigation", "status"]
    },
    {
        "question": "My payment transaction was unsuccessful",
        "answer": "Kindly note that your payment transaction was unsuccessful on this invoice. Reach out or contact your bank to log a complaint or seek a reversal.",
        "keywords": ["payment", "unsuccessful", "transaction", "bank"]
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
                    href = f'https://{url[4:]}'
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

def simple_faq_search(query: str, max_results: int = 3):
    """Simple keyword-based FAQ search"""
    query_lower = query.lower()
    matches = []
    
    for faq in vreg_faqs:
        score = 0
        for keyword in faq['keywords']:
            if keyword.lower() in query_lower:
                score += 1
        
        if score > 0:
            matches.append({
                'question': faq['question'],
                'answer': faq['answer'],
                'score': score
            })
    
    # Sort by score and return top matches
    matches.sort(key=lambda x: x['score'], reverse=True)
    return matches[:max_results]

def extract_name_from_message(message: str) -> str:
    """Extract name from user message"""
    message_lower = message.lower().strip()
    
    # Common patterns for name introduction
    name_patterns = [
        r"my name is\s+(\w+)",
        r"i'm\s+(\w+)",
        r"i am\s+(\w+)",
        r"call me\s+(\w+)",
        r"it's\s+(\w+)",
        r"this is\s+(\w+)",
        r"name:\s*(\w+)",
        r"^(\w+)$"
    ]
    
    non_names = [
        'hi', 'hello', 'hey', 'good', 'morning', 'afternoon', 'evening',
        'yes', 'no', 'ok', 'okay', 'sure', 'please', 'help', 'thanks', 'thank',
        'what', 'how', 'when', 'where', 'why', 'who', 'which',
        'vreg', 'registration', 'vehicle', 'portal', 'login', 'password',
        'payment', 'certificate', 'support', 'problem', 'issue', 'error'
    ]
    
    for pattern in name_patterns:
        match = re.search(pattern, message_lower)
        if match:
            potential_name = match.group(1).strip()
            
            if pattern == r"^(\w+)$":
                original_word = message.strip()
                if (len(potential_name) >= 2 and 
                    potential_name.lower() not in non_names and 
                    original_word[0].isupper() and
                    original_word.isalpha()):
                    return potential_name.capitalize()
            else:
                if (len(potential_name) >= 2 and 
                    potential_name.lower() not in non_names):
                    return potential_name.capitalize()
    
    return None

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message")
    conversation_id = request.json.get("conversation_id", "default")
    
    if not user_input:
        return jsonify({"error": "No message received"}), 400
    
    try:
        # Get or create conversation
        conversation = conversation_manager.get_or_create_conversation(conversation_id)
        user_name = conversation.get('user_name')
        
        # If no name in conversation, check if this is a name response
        if not user_name:
            extracted_name = extract_name_from_message(user_input)
            if extracted_name:
                conversation_manager.set_user_name(conversation_id, extracted_name)
                user_name = extracted_name
                response = f"Hello {user_name}! It's nice to meet you. How can I assist you today with the VREG platform? Do you have any questions, need help with vehicle registration, or perhaps you're experiencing some issues that you'd like me to help resolve?"
                processed_response = HyperlinkProcessor.convert_to_hyperlinks(response)
                return jsonify({
                    "reply": processed_response,
                    "raw_reply": response,
                    "relevant_faqs": [],
                    "context_used": False,
                    "name_captured": True,
                    "conversation_id": conversation_id
                })
            else:
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
        
        # Search for relevant FAQs
        relevant_faqs = simple_faq_search(user_input)
        
        # Prepare context
        context = ""
        if relevant_faqs:
            context = "Here are some relevant FAQs from the VREG knowledge base:\n\n"
            for i, faq in enumerate(relevant_faqs, 1):
                context += f"{i}. Q: {faq['question']}\n   A: {faq['answer']}\n\n"
        
        # Create system prompt
        name_context = f"The user's name is {user_name}. Use their name naturally in your responses when appropriate." if user_name else ""
        
        system_prompt = f"""You are a helpful AI assistant for the VREG (National Vehicle Registry) platform in Nigeria. 

{name_context}

Your role is to help users with vehicle registration, VIN validation, payment issues, customs clearance, and other VREG-related queries.

INSTRUCTIONS:
1. Use the provided knowledge base to answer questions when relevant
2. Be helpful, professional, and specific in your responses
3. If you don't have specific information, guide users to contact support@vreg.gov.ng or payments@vreg.gov.ng
4. Always maintain a helpful and courteous tone
5. Provide step-by-step instructions when applicable
6. When mentioning websites or email addresses, use the exact format provided
7. Keep responses concise but warm - aim for 2-3 short paragraphs maximum

IMPORTANT CONTACT INFORMATION:
- General support: support@vreg.gov.ng
- Payment issues: payments@vreg.gov.ng
- Website: www.vreg.gov.ng
- TIN validation: www.trade.gov.ng (Agencies > FIRS)"""
        
        # Create user prompt
        if context:
            user_prompt = f"{context}\n\nUser Question: {user_input}\n\nPlease provide a helpful response based on the FAQ context above and your knowledge of VREG processes."
        else:
            user_prompt = f"User Question: {user_input}\n\nPlease provide a helpful response about VREG processes."
        
        # Generate response using Groq
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="llama-3.1-8b-instant",
            temperature=0.1,
            max_tokens=800,
            top_p=0.9,
            frequency_penalty=0.1
        )
        
        raw_response = chat_completion.choices[0].message.content
        processed_response = HyperlinkProcessor.convert_to_hyperlinks(raw_response)
        
        return jsonify({
            "reply": processed_response,
            "raw_reply": raw_response,
            "relevant_faqs": relevant_faqs,
            "context_used": bool(context),
            "user_name": user_name,
            "conversation_id": conversation_id
        })
    
    except Exception as e:
        print(f"❌ Error in chat endpoint: {e}")
        error_message = "I apologize, but I'm having trouble processing your request right now. Please contact support@vreg.gov.ng for assistance."
        return jsonify({
            "reply": HyperlinkProcessor.convert_to_hyperlinks(error_message),
            "raw_reply": error_message,
            "relevant_faqs": [],
            "context_used": False
        })

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "total_faqs": len(vreg_faqs),
        "hyperlink_processing": "enabled",
        "session_support": "enabled"
    })

@app.route("/search", methods=["POST"])
def search_faqs():
    """Endpoint to search FAQs directly"""
    query = request.json.get("query")
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    try:
        relevant_faqs = simple_faq_search(query, 5)
        return jsonify({"faqs": relevant_faqs})
    except Exception as e:
        print(f"❌ Error in search endpoint: {e}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)