from PIL import Image
import os

p1 = r"C:\Users\Tamiluuu\.gemini\antigravity\brain\29e61130-dff4-4f6b-b0da-16a509f3c66b\.user_uploaded\media_1794999946087_c0b97459.jpg"
p2 = r"C:\Users\Tamiluuu\.gemini\antigravity\brain\29e61130-dff4-4f6b-b0da-16a509f3c66b\.user_uploaded\media_1794999946096_1d371360.jpg"

im1 = Image.open(p1)
im2 = Image.open(p2)

print("Image 1:", im1.size, im1.format)
print("Image 2:", im2.size, im2.format)
