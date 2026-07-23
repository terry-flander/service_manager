#!/bin/bash
# fetch_tfb_images.sh
# Run this on your Mac from the root of your local repo.
# Images will be saved to ./static/img/site/

mkdir -p ./static/img/site

curl -L -o "./static/img/site/IMG_0903.jpg" \
  "https://theflyingbike.com.au/wp-content/uploads/2019/10/IMG_0903.jpg"

curl -L -o "./static/img/site/tfb_logo_60.png" \
  "https://theflyingbike.com.au/wp-content/uploads/2017/04/tfb_logo_60.png"

curl -L -o "./static/img/site/IMG_1398-960x720.jpg" \
  "https://theflyingbike.com.au/wp-content/uploads/2019/10/IMG_1398-960x720.jpg"

curl -L -o "./static/img/site/IMG_8445.jpg" \
  "https://theflyingbike.com.au/wp-content/uploads/2019/10/IMG_8445.jpg"

curl -L -o "./static/img/site/team.jpg" \
  "https://theflyingbike.com.au/wp-content/uploads/2015/12/12188110_456830857853654_4704240768050235007_o-e1451007108595.jpg"

curl -L -o "./static/img/site/IMG_4264.jpg" \
  "https://theflyingbike.com.au/wp-content/uploads/2016/03/IMG_4264.jpg"

curl -L -o "./static/img/site/OSN4FUCU8C.jpg" \
  "https://theflyingbike.com.au/wp-content/uploads/2013/10/OSN4FUCU8C.jpg"

echo ""
echo "Downloaded:"
ls -lh ./static/img/site/
