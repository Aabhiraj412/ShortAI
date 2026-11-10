"""
Generates royalty-free ambient background music and sound effects (Whoosh, Chime)
using pure Python standard library (wave, math, struct). 100% offline, CPU-friendly.
"""
import math
import os
import struct
import wave

SAMPLE_RATE = 44100
MUSIC_DIR = r"D:\Agents\CrazzyDocs\assets\audio\music"
SFX_DIR = r"D:\Agents\CrazzyDocs\assets\audio\sfx"

os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(SFX_DIR, exist_ok=True)

def write_wav(path, samples_left, samples_right=None):
    if samples_right is None:
        samples_right = samples_left
    with wave.open(path, 'w') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2) # 16-bit
        wf.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for l, r in zip(samples_left, samples_right):
            # Clamp to 16-bit int
            cl = max(-32767, min(32767, int(l * 32767)))
            cr = max(-32767, min(32767, int(r * 32767)))
            frames.extend(struct.pack('<hh', cl, cr))
        wf.writeframes(frames)
    print(f"Generated audio: {path} ({len(samples_left)/SAMPLE_RATE:.1f}s)")

def generate_ambient_music(duration=65.0):
    """Generates an ambient lo-fi / tech pad chord progression with subtle pulse."""
    total_samples = int(SAMPLE_RATE * duration)
    left = [0.0] * total_samples
    right = [0.0] * total_samples
    
    # Chord progression: Dm9 -> Bbmaj7 -> Fmaj7 -> C
    # Root frequencies (Hz)
    chords = [
        [146.83, 220.00, 261.63, 329.63, 440.00], # Dm9
        [116.54, 174.61, 233.08, 293.66, 349.23], # Bbmaj7
        [174.61, 220.00, 261.63, 349.23, 440.00], # Fmaj7
        [130.81, 164.81, 196.00, 261.63, 329.63]  # C
    ]
    chord_len = 8.0 # seconds per chord
    
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        chord_idx = int((t % (chord_len * len(chords))) / chord_len)
        chord_notes = chords[chord_idx]
        
        # Subtle 2Hz rhythm pulse (breathing synth)
        pulse = 0.7 + 0.3 * math.sin(2 * math.pi * 2.0 * t)
        
        val_l = 0.0
        val_r = 0.0
        for idx, freq in enumerate(chord_notes):
            # Warm soft sine + subtle second harmonic
            wave_val = 0.7 * math.sin(2 * math.pi * freq * t) + 0.2 * math.sin(2 * math.pi * freq * 2.0 * t)
            # Gentle stereo panning across notes
            pan = (idx / (len(chord_notes) - 1)) # 0.0 to 1.0
            val_l += wave_val * (1.0 - pan * 0.4)
            val_r += wave_val * (0.6 + pan * 0.4)
            
        val_l /= len(chord_notes)
        val_r /= len(chord_notes)
        
        # Fade in first 2s, fade out last 3s
        envelope = 1.0
        if t < 2.0:
            envelope = t / 2.0
        elif t > (duration - 3.0):
            envelope = (duration - t) / 3.0
            
        left[i] = val_l * pulse * envelope * 0.28
        right[i] = val_r * pulse * envelope * 0.28
        
    out_path = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")
    write_wav(out_path, left, right)

def generate_whoosh_sfx(duration=0.8):
    """Generates a cinematic whoosh transition sound effect."""
    total_samples = int(SAMPLE_RATE * duration)
    samples = [0.0] * total_samples
    
    # Frequency sweep with noise-like granular texture
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        progress = t / duration
        # Bell curve volume envelope
        vol = math.sin(math.pi * progress) ** 2
        # Frequency sweep from 250Hz up to 1800Hz and back down
        center_freq = 250.0 + 1550.0 * math.sin(math.pi * progress)
        # Synthesize multi-band sweep
        s = 0.5 * math.sin(2 * math.pi * center_freq * t) + \
            0.3 * math.sin(2 * math.pi * (center_freq * 1.5) * t) + \
            0.2 * math.sin(2 * math.pi * (center_freq * 0.6) * t)
        samples[i] = s * vol * 0.55
        
    out_path = os.path.join(SFX_DIR, "whoosh_transition.wav")
    write_wav(out_path, samples)

def generate_chime_sfx(duration=0.9):
    """Generates a pleasant notification chime / text pop sound."""
    total_samples = int(SAMPLE_RATE * duration)
    samples = [0.0] * total_samples
    
    f1, f2, f3 = 587.33, 880.00, 1174.66 # D5, A5, D6
    for i in range(total_samples):
        t = i / SAMPLE_RATE
        decay = math.exp(-4.5 * t)
        s = 0.5 * math.sin(2 * math.pi * f1 * t) + \
            0.35 * math.sin(2 * math.pi * f2 * t) + \
            0.25 * math.sin(2 * math.pi * f3 * t)
        samples[i] = s * decay * 0.5
        
    out_path = os.path.join(SFX_DIR, "chime_pop.wav")
    write_wav(out_path, samples)

if __name__ == "__main__":
    generate_ambient_music(65.0)
    generate_whoosh_sfx(0.8)
    generate_chime_sfx(0.9)
    print("All audio assets created successfully!")
