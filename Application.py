import streamlit as st
import cv2
import torch
import numpy as np
import torch.nn.functional as F
from torchvision import transforms
import av
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, RTCConfiguration
from models import PerformanceModel
from emotionoverlay import EmotionOverlay
from gifoverlay import GifEmotionOverlay


# Set page config
st.set_page_config(page_title="Real-time Facial Emotion Recognition", layout="wide")
st.title("Real-time Facial Emotion Recognition")

# Load model only once
@st.cache_resource
def load_model():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model_path = "model/ferplus_model_pd_acc.pth"
    model = PerformanceModel(input_shape=(1, 48, 48), n_classes=8, logits=True).to(device)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()
    return model, device

# Global variables
model, device = load_model()
emotions = ["Neutral", "Happy", "Surprise", "Sad", "Angry", "Disgust", "Fear", "Contempt"]
haar_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Resize((48, 48)),
    transforms.Normalize(mean=[0.5], std=[0.5])
])
gif_overlay = GifEmotionOverlay("EmojiGif/")

# Emotion color definitions
emotion_text_colors = {
    "Neutral": [(255,255,255), (224,212,196), (228,203,179)],
    "Happy": [(182,110,68), (76,235,253), (83,169,242)],
    "Surprise": [(247,255,0), (42,42,165), (232,206,0)],
    "Sad": [(194,105,3), (228,172,32), (237,202,162)],
    "Angry": [(61, 57, 242), (49,121,249), (232,220,214)],
    "Disgust": [(70,190,77), (120,159,6), (100,55,124)],
    "Fear": [(198, 128, 134), (133,71,68), (80,45,98)],
    "Contempt": [(160, 134, 72), (145, 180, 250), (173, 217, 251)]
}

emotion_colors = {
    "Neutral": (255, 255, 255),  # White
    "Happy": (0, 255, 255),      # Yellow
    "Surprise": (0, 165, 255),   # Orange
    "Sad": (255, 0, 0),          # Blue
    "Angry": (0, 0, 255),        # Red
    "Disgust": (128, 0, 128),    # Purple
    "Fear": (255, 255, 0),       # Cyan
    "Contempt": (0, 255, 0)      # Green
}

# Add performance options in sidebar
st.sidebar.title("Performance Settings")
detect_frequency = st.sidebar.slider("Face Detection Frequency", 1, 10, 5, 
                                    help="Higher values = less frequent detection = better performance")
resolution_factor = st.sidebar.slider("Resolution Scale", 0.5, 1.0, 0.75, 0.05,
                                     help="Lower values = smaller resolution = better performance")
show_all_emotions = st.sidebar.checkbox("Show All Emotions", True,
                                      help="Uncheck to only display top emotion for better performance")

class VideoProcessor(VideoProcessorBase):
    def __init__(self):
        self.frame_count = 0
        self.color_index = 0
        self.animation_offset = 0
        self.offset_direction = 1
        self.faces = []
        self.last_frame_time = 0
        self.processing_fps = 0
        
    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        
        # Downscale image for processing (improves performance)
        h, w = img.shape[:2]
        img_small = cv2.resize(img, (int(w * resolution_factor), int(h * resolution_factor)))
        scale_factor = 1 / resolution_factor
        
        # Process frame
        gray = cv2.cvtColor(img_small, cv2.COLOR_BGR2GRAY)
        
        # Color cycling
        if self.frame_count % 10 == 0:  
            self.color_index = (self.color_index + 1) % 3
            
        # Run face detection less frequently for better performance
        if self.frame_count % detect_frequency == 0:
            self.faces = haar_cascade.detectMultiScale(
                gray, 
                scaleFactor=1.2, 
                minNeighbors=5, 
                minSize=(int(48 * resolution_factor), int(48 * resolution_factor))
            )
            
        self.frame_count += 1
        
        # Scale back face coordinates to original image size
        scaled_faces = [(int(x * scale_factor), int(y * scale_factor), 
                         int(w * scale_factor), int(h * scale_factor)) for (x, y, w, h) in self.faces]
        
        for (x, y, w, h) in scaled_faces:
            # Extract face from original image
            face_region = img[y:y+h, x:x+w]
            if face_region.size == 0:  # Skip if face region is invalid
                continue
                
            face_gray = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY)
            face_resized = cv2.resize(face_gray, (48, 48))
            face_tensor = torch.tensor(face_resized, dtype=torch.float32).div(255).sub(0.5).div(0.5).unsqueeze(0).unsqueeze(0).to(device)
            
            # Run model inference
            with torch.no_grad():
                outputs = model(face_tensor)
                probs = F.softmax(outputs, dim=1)[0].cpu().numpy()
                top_emotion_idx = np.argmax(probs)
                top_emotion = emotions[top_emotion_idx]
            
            # Animation offset logic
            self.animation_offset += self.offset_direction * 2
            if abs(self.animation_offset) > 10:
                self.offset_direction *= -1
            
            # Overlay character
            img = gif_overlay.overlay_gif(img, top_emotion, x, y, w, h, self.animation_offset)
            
            # Draw rectangle
            box_color = emotion_colors.get(top_emotion, (255, 255, 255))
            cv2.rectangle(img, (x, y), (x + w, y + h), box_color, 2)
            
            # Display either all emotions or just the top one based on settings
            if show_all_emotions:
                for i, (emotion, prob) in enumerate(zip(emotions, probs)):
                    if i == top_emotion_idx:
                        text_color = emotion_text_colors[top_emotion][self.color_index]
                    else:
                        text_color = (255, 255, 255)
                    
                    text = f"{emotion}: {int(prob * 100)}%"
                    cv2.putText(img, text, (x, y - 10 - (i * 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 2)
            else:
                # Only show top emotion
                text_color = emotion_text_colors[top_emotion][self.color_index]
                text = f"{top_emotion}: {int(probs[top_emotion_idx] * 100)}%"
                cv2.putText(img, text, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, text_color, 2)
        
        return av.VideoFrame.from_ndarray(img, format="bgr24")

# Configure WebRTC
rtc_configuration = RTCConfiguration(
    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)

# Display WebRTC component
webrtc_ctx = webrtc_streamer(
    key="facial-emotion",
    video_processor_factory=VideoProcessor,
    rtc_configuration=rtc_configuration,
    media_stream_constraints={"video": {"width": {"ideal": 640}, "height": {"ideal": 480}, "frameRate": {"max": 30}}, "audio": False},
    async_processing=True,
)

with st.expander("About this app"):
    st.write("""
    This app performs real-time facial emotion recognition using a trained deep learning model.
    It detects faces and classifies emotions into 8 categories: Neutral, Happy, Surprise, Sad, Angry, Disgust, Fear, and Contempt.
    
    The app overlays emotion-specific GIFs and displays the probability for each emotion.
    
    For better performance, adjust the settings in the sidebar:
    - Lower the face detection frequency
    - Reduce the resolution scale
    - Disable showing all emotions
    """)