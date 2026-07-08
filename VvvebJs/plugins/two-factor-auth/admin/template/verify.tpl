import(common.tpl)

.settings input[type="text"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['stripe'][$_setting]  ?? Vvveb\getSetting('stripe', $_setting, null) ?? '@@__value__@@');
	//name="stripe[setting-name] > get only setting-name
?>

.settings input[type="password"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['stripe'][$_setting]  ?? Vvveb\getSetting('stripe', $_setting, null) ?? '@@__value__@@');
	//name="stripe[setting-name] > get only setting-name
?>

.settings input[type="number"]|value = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['stripe'][$_setting]  ?? Vvveb\getSetting('stripe',$_setting, null) ?? '@@__value__@@');
	//name="stripe[setting-name] > get only setting-name
?>

.settings input[type="radio"]|addNewAttribute = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	$_value = '@@__value__@@';
	
	if (isset($_POST['stripe'][$_setting]) && ($_POST['stripe'][$_setting] == $_value) ||
		(Vvveb\getSetting('stripe',$_setting, null) == $_value)  ||
		 '@@__checked__@@') { 
			echo 'checked';
	}
?>

.settings textarea = <?php 
	$_setting = '@@__name:\[(.*)\]__@@';
	echo htmlspecialchars($_POST['stripe'][$_setting] ?? Vvveb\getSetting('stripe',$_setting, null) ?? '@@__innerHTML__@@');
?>



/* language tabs */

[data-v-languages]|before = <?php $_lang_instance = '@@__data-v-languages__@@';$_i = 0;?>
@language = [data-v-languages] [data-v-language]
@language|deleteAllButFirstChild
//@language|addClass = <?php if ($_i == 0) echo 'active';?>

@language|before = <?php

foreach ($this->languagesList as $language) { ?>
	[data-v-languages] [data-v-language-id]|id = <?php echo 'lang-' . $language['code'] . '-' . $_lang_instance;?>
	[data-v-languages]  [data-v-language-id]|addClass = <?php if ($_i == 0) echo 'show active';?>

	@language [data-v-language-name] = $language['name']
	@language [data-v-language-img]|title = $language['name']
	@language [data-v-language-img]|src = <?php echo 'language/' . $language['code'] . '/' . $language['code'] . '.png';?>
	@language [data-v-language-link]|href = <?php echo '#lang-' . $language['code'] . '-' . $_lang_instance?>
	@language [data-v-language-link]|addClass = <?php if ($_i == 0) echo 'active';?>

	@language [data-v-lang-*] = <?php 
		$key = '@@__name:lang\[([^\]]+)\]__@@';
		$name = '@@__data-v-lang-(*)__@@';
		echo htmlspecialchars($this->lang[$language['language_id']][$key][$name] ?? $_POST[$language['language_id']][$key][$name] ?? '');
	?>

	@language [data-v-lang-*]|name = <?php 
		$key = '@@__name:lang\[([^\]]+)\]__@@';
		$name = '@@__data-v-lang-(*)__@@';
		echo "lang[$key][{$language['language_id']}][$name]";
	?>	

@language|after = <?php 
$_i++;
}
?>




@region_group = [data-v-region_group_id] [data-v-option]
@region_group|deleteAllButFirstChild

@region_group|before = <?php
$count = 0;
if(isset($this->region_group) && is_array($this->region_group)) {
	foreach ($this->region_group as $region_group_index => $region_group) {?>
	
	
	@region_group|innerText = $region_group
	@region_group|value	= $region_group_index
	@region_group|addNewAttribute = <?php if (isset($this->region_group_id) && $region_group_index == $this->region_group_id) echo 'selected';?>
	
	
	@region_group|after = <?php 
		$count++;
	} 
}?>

@tax_type = [data-v-tax_type_id] [data-v-option]
@tax_type|deleteAllButFirstChild

@tax_type|before = <?php
$count = 0;
if(isset($this->tax_type) && is_array($this->tax_type)) {
	foreach ($this->tax_type as $tax_type_index => $tax_type) {?>
	
	
	@tax_type|innerText = $tax_type
	@tax_type|value	    = $tax_type_index
	@tax_type|addNewAttribute = <?php if (isset($this->tax_type_id) && $tax_type_index == $this->tax_type_id) echo 'selected';?>
	
	
	@tax_type|after = <?php 
		$count++;
	} 
}?>

@payment_status = [data-v-payment_status_id] [data-v-option]
@payment_status|deleteAllButFirstChild

@payment_status|before = <?php
$count = 0;
if(isset($this->payment_status) && is_array($this->payment_status)) {
	foreach ($this->payment_status as $payment_status_index => $payment_status) {?>
	
	
	@payment_status|innerText = <?php echo htmlspecialchars(Vvveb\humanReadable($payment_status));?>
	@payment_status|value	  = $payment_status_index
	@payment_status|addNewAttribute = <?php if (isset($this->payment_status_id) && $payment_status_index == $this->payment_status_id) echo 'selected';?>
	
	
	@payment_status|after = <?php 
		$count++;
	} 
}?>
