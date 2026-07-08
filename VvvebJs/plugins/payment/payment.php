<?php

/**
 * Vvveb
 *
 * Copyright (C) 2022  Ziadin Givan
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

namespace Vvveb\Plugins\Payment;

use Vvveb\Sql\Region_groupSQL;
use Vvveb\System\PaymentMethod;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class Payment extends PaymentMethod {
	private $namespace = 'payment';

	private $method_data = [
		'name'            => 'payment',
		'title'           => 'Cash on delivery',
		'description'     => 'Pay cash on delivery',
		'text'            => '',
		'region_id'       => 0,
		'cost'            => 0,
		'tax_type_id'     => 0,
		'region_group_id' => 0,
	];

	public function getMethod(&$checkoutInfo = [], $options = []) {
		$this->method_data         = $options + $this->method_data;
		$this->method_data['cost'] = floatval($this->method_data['cost']);
		//$this->getCost();

		//if set only for specific region(s) check if address matches region
		$region_group_id     = $this->method_data['region_group_id'];
		$shipping_country_id = $checkoutInfo['shipping_country_id'] ?? false;
		$shipping_region_id  = $checkoutInfo['shipping_region_id'] ?? 0;

		if ($region_group_id != 0) {
			$region  = new Region_groupSQL();
			$regions = [];
			$params  = ['region_group_id' => (int)$region_group_id, 'country_id' => (int)$shipping_country_id, 'region_id' => (int)$shipping_region_id];
			$regions = $region->isRegion($params);

			if (isset($regions['count']) && $regions['count'] == 0) {
				return [];
			}
		}

		return $this->method_data;
	}

	public function init() {
		//remove previous total and tax
		$this->cart->removeTotal($this->namespace);
		$this->cart->removeTax($this->namespace);
	}

	public function setMethod() {
		if ($this->method_data['cost'] || $this->method_data['text']) {
			if ($this->method_data['tax_type_id']) {
				$this->cart->addTax($this->namespace, $this->method_data['cost'], $this->method_data['tax_type_id']);
			}

			$this->cart->addTotal('payment', $this->namespace, $this->method_data['title'] , $this->method_data['cost'], $this->method_data['text']);
		}
	}

	public function authorize(&$checkoutInfo = []) {
		return true;
	}
}
