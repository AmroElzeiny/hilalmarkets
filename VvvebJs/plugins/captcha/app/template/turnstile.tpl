#turnstile-js|delete
.cf-turnstile|delete

head|append = from(/public/plugins/captcha/turnstile.html|head > *)

form#reset-form .btn-reset|before   = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)
form#login-form .btn-login|before   = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)
form.login-form .btn-login|before   = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)
form#signup-form .btn-signup|before = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)

form#comment-form .btn-submit|before  = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)
form#review-form .btn-submit|before   = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)
form#question-form .btn-submit|before = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)

[data-v-component-plugin-contact-form-form] .btn-primary|before = from(/public/plugins/captcha/turnstile.html|body > .turnstile-form > *)


//don't show the captcha if page is edited
#turnstile-js|hide    = $vvveb_is_page_edit
.cf-turnstile|hide    = $vvveb_is_page_edit

.cf-turnstile|data-sitekey =  <?php echo Vvveb\getSetting('captcha', 'site_key', null) ?? '';?>
