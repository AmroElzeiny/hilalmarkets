function reloadScript(src, id) {
      var head= document.getElementsByTagName('head')[0];
      var script= document.createElement('script');
      script.src= src;
      script.id = id;
      head.appendChild(script);
      return script;
}

window.addEventListener("vvveb.loadUrl", function (e) {
	
	let elements = document.querySelectorAll(".cf-turnstile");
	if (elements.length) {
		if (typeof turnstile == "undefined") {
			let script = document.getElementById("turnstile-js");
			let js = reloadScript(script.src, script.id);
			js.onload = () => elements.forEach(e => turnstile.execute(e, {}));
			script.remove();
		} else {
			elements.forEach(e => turnstile.execute(e, {}));
		}
	}
});
