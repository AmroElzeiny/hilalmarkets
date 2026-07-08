import(common.tpl)

.settings input[type=text]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	$_default = '@@__value__@@';
	echo htmlspecialchars($_POST['settings'][$_setting] ?? (Vvveb\getSetting('insert-scripts', $_setting, '', $this->site_id) ?: $_default));
	//name="settings[setting-name] > get only setting-name
?>

.settings textarea = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	$_default = '@@__value__@@';
	echo htmlspecialchars($_POST['settings'][$_setting] ?? (Vvveb\getSetting('insert-scripts', $_setting, '', $this->site_id) ?: $_default));
?>
