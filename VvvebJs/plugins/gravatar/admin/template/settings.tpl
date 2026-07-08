import(common.tpl)

.settings input[type="text"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['gravatar'][$_setting] ?? Vvveb\getSetting('gravatar', $_setting, null) ?? '@@__value__@@');
	//name="gravatar[setting-name] > get only setting-name
?>

.settings input[type="number"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['gravatar'][$_setting] ?? Vvveb\getSetting('gravatar',$_setting, null) ?? '@@__value__@@');
	//name="gravatar[setting-name] > get only setting-name
?>

.settings input[type="radio"]|addNewAttribute = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	$_value = '@@__value__@@';
	
	if (isset($_POST['gravatar'][$_setting]) && ($_POST['gravatar'][$_setting] == $_value) ||
		(Vvveb\getSetting('gravatar',$_setting, null) == $_value)  ||
		 '@@__checked__@@') { 
			echo 'checked';
	}
?>

.settings textarea = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['gravatar'][$_setting] ?? Vvveb\getSetting('gravatar',$_setting, null) ?? '@@__innerHTML__@@');
?>
