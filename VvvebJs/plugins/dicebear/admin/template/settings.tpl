import(common.tpl)

.settings input[type="text"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['settings'][$_setting] ?? Vvveb\getSetting('dicebear', $_setting, null) ?? '@@__value__@@');
	//name="dicebear[setting-name] > get only setting-name
?>

.settings input[type="number"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['settings'][$_setting] ?? Vvveb\getSetting('dicebear',$_setting, null) ?? '@@__value__@@');
	//name="dicebear[setting-name] > get only setting-name
?>

.flip input|addNewAttribute = <?php 
$flip = Vvveb\getSetting('dicebear','flip', null);
if ($flip == '@@__value__@@') echo 'checked';
?>

.style|deleteAllButFirst

.style|before = <?php 
$savedStyle = Vvveb\getSetting('dicebear','style', null);

foreach ($this->styles as $style) { ?>
	
	.style img|src = <?php echo "https://api.dicebear.com/7.x/$style/svg?seed=JD&flip=$flip";?>
	.style span = <?php echo Vvveb\humanReadable($style);?>
	.style input = $style
	.style input|addNewAttribute = <?php 
		
		if (isset($_POST['dicebear']['style']) && ($_POST['dicebear']['style'] == $style) ||
			($savedStyle == $style) ) { 
				echo 'checked';
		}
	?>

.style|after = <?php
	} 
?>
