import(common.tpl)


/* method tabs */
.settings|before = <?php $index = 0;?>
@method = [data-v-methods] [data-v-method]
@method|deleteAllButFirstChild

@method|before = <?php
if (isset($this->methods) && is_array($this->methods))
foreach ($this->methods as $index => $method) { ?>

	[data-v-methods] .tab-pane[data-v-method]|id = <?php echo "tab-$index";?>
	[data-v-methods] .tab-pane[data-v-method]|addClass = <?php if ($index == 0) echo "active";?>

	@method [data-v-method-shipping_method_id]|href = <?php echo "#tab-$index";?>
	@method [data-v-method-shipping_method_id]|addClass = <?php if ($index == 0) echo "active";?>
	@method [data-v-method-name] = <?php $langa = current($method['lang'] ?? []);echo htmlspecialchars($langa['title'] ?: "Method  $index");?>

/*
	@method input.method|value = <?php
		$_setting = '@@__name:\]\[([^\]]+)\]__@@';
		echo htmlspecialchars($_POST['settings'][$_setting] ?? $method[$_setting] ?? '@@__value__@@');
	?>
*/

	@method input.method|name = <?php
		$name = '@@__name__@@';
		echo htmlspecialchars(str_replace('methods[0]',"methods[$index]", $name));
	?>	
	
	@method select|name = <?php
		$name = '@@__name__@@';
		echo htmlspecialchars(str_replace('methods[0]',"methods[$index]", $name));
	?>

@method|after = <?php 
	}

$index = 0;	
$method = [];
?>

@shipping_status = [data-v-shipping_status_id] [data-v-option]
@shipping_status|deleteAllButFirstChild

@shipping_status|before = <?php
$count = 0;
if(isset($this->shipping_status) && is_array($this->shipping_status)) {
	foreach ($this->shipping_status as $shipping_status_index => $shipping_status) {?>
	
	@shipping_status|innerText = $shipping_status
	@shipping_status|value	  = $shipping_status_index
	@shipping_status|addNewAttribute = <?php if (isset($method['shipping_status_id']) && $shipping_status_index == $method['shipping_status_id']) echo 'selected';?>
	
	@shipping_status|after = <?php 
		$count++;
	} 
}?>

/* language tabs */

[data-v-languages]|before = <?php $_lang_instance = '@@__data-v-languages__@@';$_i = 0;?>
@language = [data-v-languages] [data-v-language]
@language|deleteAllButFirstChild
//@language|addClass = <?php if ($_i == 0) echo 'active';?>

@language|before = <?php

foreach ($this->languagesList as $language) { ?>
	[data-v-languages] [data-v-language-id]|id = <?php echo "lang-$index-" . $language['code'] . '-' . $_lang_instance;?>
	[data-v-languages]  [data-v-language-id]|addClass = <?php if ($_i == 0) echo 'show active';?>

	@language [data-v-language-name] = $language['name']
	@language [data-v-language-img]|title = $language['name']
	@language [data-v-language-img]|src = <?php echo 'language/' . $language['code'] . '/' . $language['code'] . '.png';?>
	@language [data-v-language-link]|href = <?php echo "#lang-$index-" . $language['code'] . '-' . $_lang_instance?>
	@language [data-v-language-link]|addClass = <?php if ($_i == 0) echo 'active';?>

	@language [data-v-lang-*] = <?php 
		$name = '@@__data-v-lang-(*)__@@';
		//var_dump($method['lang'][0][$language['language_id']]);
		echo htmlspecialchars($method['lang'][$language['language_id']][$name] ?? $_POST[$index][$language['language_id']][$name] ?? '')
	?>

	@language [data-v-lang-*]|name = <?php 
		$name = '@@__data-v-lang-(*)__@@';
		echo "methods[$index][lang][{$language['language_id']}][$name]";
	?>	

@language|after = <?php 
$_i++;
}
?>


@weight = [data-v-weights] [data-v-weight]
@weight|deleteAllButFirstChild

@weight|before = <?php
$count = 0;
if(isset($method['weight']) && is_array($method['weight'])) {
	foreach ($method['weight'] as $weight_index => $weight) {?>
	
	@weight [data-v-weight-*]           = $weight['@@__data-v-weight-(*)__@@']
	@weight input[data-v-weight-*]|name = <?php echo "methods[$index][weight][$weight_index][@@__data-v-weight-(*)__@@]";?>
	
	@weight|after = <?php 
		$count++;
	} 
}?>


@region_group = [data-v-region_group_id] [data-v-option]
@region_group|deleteAllButFirstChild

@region_group|before = <?php
$count = 0;
if(isset($this->region_group) && is_array($this->region_group)) {
	foreach ($this->region_group as $region_group_index => $region_group) {?>
	
	
	@region_group|innerText = $region_group
	@region_group|value	    = $region_group_index
	@region_group|addNewAttribute = <?php if (isset($method['region_group_id']) && $region_group_index == $method['region_group_id']) echo 'selected';?>
	
	
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
	@tax_type|addNewAttribute = <?php if (isset($method['tax_type_id']) && $tax_type_index == $method['tax_type_id']) echo 'selected';?>
	
	
	@tax_type|after = <?php 
		$count++;
	} 
}?>


@order_status = [data-v-order_status_id] [data-v-option]
@order_status|deleteAllButFirstChild

@order_status|before = <?php
$count = 0;
if(isset($this->order_status) && is_array($this->order_status)) {
	foreach ($this->order_status as $order_status_index => $order_status) {?>
	
	
	@order_status|innerText = <?php echo Vvveb\humanReadable($order_status);?>
	@order_status|value     = $order_status_index
	@order_status|addNewAttribute = <?php if (isset($method['order_status_id']) && $order_status_index == $method['order_status_id']) echo 'selected';?>
	
	
	@order_status|after = <?php 
		$count++;
	} 
}?>
