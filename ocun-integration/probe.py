import os
import urllib.request
from html.parser import HTMLParser

class LoginFields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.password=False
    def handle_starttag(self,tag,attrs):
        if tag=="input" and dict(attrs).get("type","").lower()=="password":
            self.password=True

def check():
    if os.getenv("OCUN_READ_ONLY")!="true" or os.getenv("OCUN_IMPORT_ENABLED")!="false":
        return "SAFE_MODE_REQUIRED"
    if not os.getenv("OCUN_USERNAME") or not os.getenv("OCUN_PASSWORD"):
        return "CREDENTIALS_MISSING"
    try:
        with urllib.request.urlopen("https://sales.ocun.com/",timeout=15) as response:
            page=response.read(200000).decode("utf-8","replace")
        parser=LoginFields()
        parser.feed(page)
        return "LOGIN_FORM_VISIBLE" if parser.password else "LOGIN_FORM_NOT_FOUND"
    except Exception:
        return "NETWORK_ERROR"

if __name__=="__main__":
    print("OCUN_READONLY_DIAGNOSTIC="+check(),flush=True)
