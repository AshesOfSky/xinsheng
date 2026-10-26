"""Draw the app's simple fish mark; no downloaded branding assets."""
from pathlib import Path
from PIL import Image, ImageDraw

destination = Path(__file__).parent / 'assets'
destination.mkdir(exist_ok=True)
image = Image.new('RGBA',(256,256),(0,0,0,0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((4,4,252,252),radius=58,fill='#17324d')
draw.polygon([(73,128),(31,77),(31,177)],fill='#58b7f1')
draw.ellipse((55,62,229,198),fill='#61baff')
draw.ellipse((174,94,198,118),fill='white')
draw.ellipse((183,99,195,113),fill='#17324d')
draw.arc((147,114,199,158),start=20,end=150,fill='#17324d',width=5)
image.save(destination/'fish.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
