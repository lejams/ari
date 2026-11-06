"""WebKit (WebKitGTK through WebKitWebDriver) twin of run-chromium.mjs. See README.md."""
import json, os, sys, time
from urllib.parse import quote, urlparse
HERE = os.path.dirname(os.path.abspath(__file__))
from selenium import webdriver
from selenium.webdriver.webkitgtk.options import Options
from selenium.webdriver.common.by import By
base = os.environ.get("BASE", "http://127.0.0.1:8010")
# The narrow-screen wrapper must be on the same site as BASE (same host name), or SameSite=Strict cookies never reach the iframe.
FRAME_HOST = urlparse(base).hostname or "127.0.0.1"
acc = json.load(open(os.environ.get("ARI_QA_ACCOUNT_FILE", "accounts.json")))[0]
measure = "\n".join(l for l in open(os.path.join(HERE, "measure.js")).read().splitlines() if not l.strip().startswith("//")).strip().rstrip(";")
perf = "\n".join(l for l in open(os.path.join(HERE, "scrollperf.js")).read().splitlines() if not l.strip().startswith("//")).strip().rstrip(";")
axe = open(os.environ["ARI_QA_AXE"]).read()
only = os.environ.get("ONLY", "").split(",") if os.environ.get("ONLY") else None
shots = os.environ.get("SHOTS", "qa-shots/webkit"); os.makedirs(shots, exist_ok=True)
out = os.environ.get("OUT", "qa-webkit.json")
VPS = [("d1920",1920,1080),("d1440",1440,900),("d1280",1280,720),("t1024",1024,768),("t768",768,1024),("m430",430,932),("m390",390,844),("m360",360,740),("m320",320,568)]
BO = os.environ.get("TARGET") == "bo"
PAGES = [("landing","/",None),("login","/connexion",None)] + [("app-"+v,"/app",v) for v in ["home","programme","cases","history","vocab","progress","exercises","exam","placement","drill"]] + [("voice-ready","/voice.html?case=ARI-DEV-FR&mode=training","voice"),("voice-exam","/voice.html?case=ARI-DEV-FR&mode=exam","voice")]
if BO: PAGES = [("bo-login","/#/login",None)] + [("bo-"+(r or "home"),"/#/"+r,"bo") for r in ["","documents","review","registry","exercises","gold","costs","accounts"]]
if os.environ.get("PAGES"):
    _po = os.environ["PAGES"].split(",")
    PAGES = [p for p in PAGES if any(p[0].startswith(x) for x in _po)]
def mk():
    o = Options(); o.binary_location = "/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1/MiniBrowser"; o.add_argument("--automation")
    return webdriver.WebKitGTK(options=o)
def sz(d, w, h):
    d.set_window_size(w, h)
    # window chrome eats some pixels: correct once
    iw, ih = d.execute_script("return [innerWidth, innerHeight]")
    if (iw, ih) != (w, h): d.set_window_size(w + (w - iw), h + (h - ih))
    return d.execute_script("return [innerWidth, innerHeight]")
def login(d):
    if BO:
        goto(d, "/#/login"); time.sleep(1)
        if d.find_elements(By.CSS_SELECTOR, "#login-email") and d.find_element(By.CSS_SELECTOR, "#login-email").is_displayed():
            d.execute_script("""const set=(el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}))};set(document.querySelector('#login-email'),arguments[0]);set(document.querySelector('#login-password'),arguments[1]);document.querySelector('#login-form button.primary, #login-form button[type=submit]').click();""",
                os.environ.get("ARI_QA_BO_EMAIL", "owner@example.test"), os.environ.get("ARI_QA_BO_PASSWORD", "correct horse battery"))
            time.sleep(1.5)
            # A wrong password leaves the login form on screen: stop instead of measuring the login page under every name.
            if d.find_elements(By.CSS_SELECTOR, "#login-email") and d.find_element(By.CSS_SELECTOR, "#login-email").is_displayed():
                raise SystemExit("Back-office login failed: check ARI_QA_BO_EMAIL and ARI_QA_BO_PASSWORD")
        return
    goto(d, "/connexion"); time.sleep(.8)
    if not d.find_elements(By.CSS_SELECTOR, "#login-form"): return
    d.execute_script("""const f=document.querySelector('#login-form');
      const set=(el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}))};
      set(f.querySelector('input[type=email]'),arguments[0]);set(f.querySelector('input[name=password]'),arguments[1]);
      [...f.querySelectorAll('button')].find(b=>b.textContent.trim()==='Se connecter').click();""", acc["email"], acc["password"])
    time.sleep(1.5)
IFR = None
def goto(d, url):
    """Open url directly, or inside an iframe of the wanted size when the window cannot be that narrow."""
    if IFR:
        d.switch_to.default_content()
        d.get(f"http://{FRAME_HOST}:8011/frame.html?w={IFR[0]}&h={IFR[1]}&src=" + quote(base + url, safe=""))
        for _ in range(20):
            time.sleep(.3)
            try: d.switch_to.frame(0); break
            except Exception: d.switch_to.default_content()
    else:
        d.get(base + url)
res = []
d = mk()
try:
    for vn, w, h in VPS:
        if only and vn not in only: continue
        IFR = (w, h) if w < 600 else None
        got = sz(d, 1400, 1000) if IFR else sz(d, w, h)
        if IFR: got = [w, h]
        for scheme in ["light", "dark"]:
            d.get(base + ("/" if BO else "/connexion")); d.delete_all_cookies(); d.execute_script("localStorage.setItem('ari-theme', arguments[0])", scheme)
            logged_in = False  # cookies were just cleared: sign in before the first protected page, whichever pages PAGES selects
            for pn, url, view in PAGES:
                if view is not None and not logged_in: login(d); logged_in = True
                if view is None and pn == "landing": d.delete_all_cookies() if False else None
                goto(d, url)
                if BO: d.execute_script("location.hash=arguments[0]", url.split("#")[1])
                elif view and view != "voice": d.execute_script("location.hash=arguments[0]", view)
                time.sleep(1.8 if pn == "landing" else 1.0)
                try:
                    m = d.execute_script("return " + measure)
                    p = {} if os.environ.get("NOPERF") else d.execute_async_script("const cb=arguments[arguments.length-1]; " + perf + ".then(cb)")
                    time.sleep(.7)
                    d.execute_script(axe)
                    ax = d.execute_async_script("const cb=arguments[arguments.length-1]; axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','best-practice']}}).then(r=>cb(r.violations.map(v=>v.impact+' '+v.id+' x'+v.nodes.length)))")
                except Exception as e:
                    m = {"vw": got[0], "vh": got[1], "hOverflow": 0, "offscreen": [], "smallTargets": [], "smallTargetCount": 0, "targetMin": 0, "iosZoomFields": [], "truncated": [], "tinyText": [], "fixed": []}; p = {}; ax = ["ERR " + str(e)[:80]]
                d.save_screenshot(f"{shots}/{vn}-{scheme}-{pn}.png")
                res.append({"engine": "webkit", "vp": vn, "scheme": scheme, "page": pn, "m": m, "perf": p, "axe": ax, "errs": [], "win": got})
            json.dump(res, open(out, "w"))
            print("done", vn, scheme, got, file=sys.stderr, flush=True)
finally:
    json.dump(res, open(out, "w")); d.quit()
