"""Reddit fetcher: new + hot posts from r/chicago and r/Chicagoland, filtered
for mobility keywords before being sent to Claude.
"""

import os
import re

import praw
from dotenv import load_dotenv

load_dotenv()

SUBREDDITS = ["chicago", "Chicagoland"]

KEYWORDS = [
    "accident", "crash", "closed", "delay", "construction", "police", "fire",
    "protest", "flooding", "Metra", "CTA", "L train", "expressway", "highway",
]

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b", re.IGNORECASE
)

POST_LIMIT = 25

_reddit = None


def _get_reddit() -> praw.Reddit:
    global _reddit
    if _reddit is None:
        _reddit = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
        )
    return _reddit


def fetch_reddit_posts() -> list[dict]:
    """Fetch new + hot posts from the configured subreddits, filtered for
    mobility keywords.
    """
    reddit = _get_reddit()

    posts = []
    seen_ids = set()

    for subreddit_name in SUBREDDITS:
        subreddit = reddit.subreddit(subreddit_name)
        for listing in (subreddit.new(limit=POST_LIMIT), subreddit.hot(limit=POST_LIMIT)):
            for submission in listing:
                if submission.id in seen_ids:
                    continue
                seen_ids.add(submission.id)

                if not _matches_keywords(submission):
                    continue

                posts.append({
                    "id": submission.id,
                    "title": submission.title,
                    "selftext": submission.selftext,
                    "subreddit": subreddit_name,
                    "created_utc": submission.created_utc,
                    "url": submission.url,
                })

    return posts


def _matches_keywords(submission) -> bool:
    text = f"{submission.title} {submission.selftext}"
    return bool(_KEYWORD_RE.search(text))


if __name__ == "__main__":
    for post in fetch_reddit_posts():
        print(post)
