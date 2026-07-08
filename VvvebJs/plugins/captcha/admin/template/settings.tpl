import(common.tpl)

.settings input[type="text"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['settings'][$_setting] ?? Vvveb\getSetting('captcha', $_setting, null) ?? '@@__value__@@');
	//name="turnstile[setting-name] > get only setting-name
?>

.settings input[type="number"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['settings'][$_setting]  ?? Vvveb\getSetting('captcha',$_setting, null) ?? '@@__value__@@');
	//name="turnstile[setting-name] > get only setting-name
?>

