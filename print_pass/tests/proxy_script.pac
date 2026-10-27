function FindProxyForURL(url, host) { if (url.indexOf("http://127.0.0.1:8123/") === 0 || url.indexOf("ws://127.0.0.1:8123/") === 0) return "DIRECT"; return "PROXY 127.0.0.1:4242"; }
