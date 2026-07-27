"""
This file contains all the constants used in the program.
"""

TWITTER_TEXTAREA_CLASS = "public-DraftStyleDefault-block public-DraftStyleDefault-ltr"
TWITTER_POST_BUTTON_XPATH = "/html/body/div[1]/div/div/div[2]/main/div/div/div/div[1]/div/div[3]/div/div[2]/div[1]/div/div/div/div[2]/div[2]/div[2]/div/div/div/div[3]"

OPTIONS = [
    "YouTube Shorts Automation",
    "Twitter Bot",
    "Affiliate Marketing",
    "Outreach",
    "Quit"
]

TWITTER_OPTIONS = [
    "Post something",
    "Show all Posts",
    "Setup CRON Job",
    "Quit"
]

TWITTER_CRON_OPTIONS = [
    "Once a day",
    "Twice a day",
    "Thrice a day",
    "Quit"
]

YOUTUBE_OPTIONS = [
    "Upload Short",
    "Show all Shorts",
    "Setup CRON Job",
    "Quit"
]

YOUTUBE_CRON_OPTIONS = [
    "Once a day",
    "Twice a day",
    "Thrice a day",
    "Quit"
]

# YouTube Section
YOUTUBE_TEXTBOX_ID = "textbox"
YOUTUBE_MADE_FOR_KIDS_NAME = "VIDEO_MADE_FOR_KIDS_MFK"
YOUTUBE_NOT_MADE_FOR_KIDS_NAME = "VIDEO_MADE_FOR_KIDS_NOT_MFK"
YOUTUBE_NEXT_BUTTON_ID = "next-button"
YOUTUBE_SHOW_MORE_BUTTON_ID = "toggle-button"
YOUTUBE_SCHEDULE_EXPAND_ID = "second-container-expand-button"
YOUTUBE_DATEPICKER_TRIGGER_ID = "datepicker-trigger"
YOUTUBE_TIME_CONTAINER_ID = "time-of-day-container"

# Visual styles: each video draws one so the channel page shows variety
# while frames within a video stay coherent
IMAGE_STYLES = [
    "hyperrealistic cinematic photography, dramatic lighting",
    "retro 90s videogame concept art, painterly brushstrokes",
    "vibrant pixel art, 16-bit aesthetic",
    "modern anime illustration, bold saturated colors",
    "dark graphic novel style, heavy ink shadows",
    "stylized 3D render, glossy materials, soft studio lighting",
    "synthwave retrofuturism, neon grid, sunset gradients",
    "epic fantasy digital painting, rich detail",
]

# Title hook formats, drawn per video so the channel page doesn't read
# like one template
TITLE_STYLES = [
    "a question that creates curiosity",
    "a numbered list teaser",
    "a bold claim that sparks debate",
    "a nobody-tells-you-this style hook",
    "a surprising fact teaser",
    "a versus or comparison framing",
]

# Random angles injected into topic generation so consecutive videos
# don't converge on the same idea
TOPIC_ANGLES = [
    "little-known facts and trivia",
    "a top 5 ranking",
    "hidden secrets and easter eggs",
    "myths that everyone believes but are false",
    "world records and extreme achievements",
    "the biggest failure or flop in this space",
    "the surprising technology behind it",
    "an iconic character, person or item",
    "mistakes beginners always make",
    "a comparison between two rivals",
    "what almost nobody remembers anymore",
    "a prediction about the near future",
    "the untold origin story",
    "the most expensive or valuable example",
]
YOUTUBE_ALTERED_CONTENT_YES_NAME = "VIDEO_HAS_ALTERED_CONTENT_YES"
YOUTUBE_RADIO_BUTTON_XPATH = "//*[@id=\"radioLabel\"]"
YOUTUBE_DONE_BUTTON_ID = "done-button"

# Amazon Section (AFM)$
AMAZON_PRODUCT_TITLE_ID = "productTitle"
AMAZON_FEATURE_BULLETS_ID = "feature-bullets"
