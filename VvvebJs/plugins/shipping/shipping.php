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

namespace Vvveb\Plugins\Shipping;

use function Vvveb\__;
use Vvveb\Sql\Region_groupSQL;
use Vvveb\System\Cart\Currency;
use Vvveb\System\Core\View;
use Vvveb\System\ShippingMethod;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class Shipping extends ShippingMethod {
	private $namespace = 'shipping';

	private $method_data = [
		'name'            => 'shipping',
		'title'           => 'Shipping',
		'description'     => 'Default shipping',
		'region_id'       => 0,
		'cost'            => 0,
		'tax_type_id'     => 0,
		'region_group_id' => 0,
		'free_shipping'   => 0,
		'difference'      => 0,
		'text'            => '',
		'free_message'    => 'Add more products worth at least %s to get free shipping!',
	];

	private function getCost() {
		$subtotal = $this->cart->getSubTotal(); //without totals/taxes
		$total    = $this->cart->getGrandTotal();

		if (($this->method_data['cost'] == 0) ||
			($this->method_data['free_shipping'] && ($total > $this->method_data['free_shipping']))) {
			$this->method_data['cost']  = 0;
		}

		if (isset($this->method_data['weight'])) {
			$cartWeight       = $this->cart->getWeight();
			$base             = $this->method_data['base-weight'];
			$additionalPrice  = 0;
			$additionalWeight = $cartWeight - $base;

			if ($additionalWeight) {
				//get heaviest first
				$weights = array_reverse($this->method_data['weight']);

				foreach ($weights as $weight) {
					if (($cartWeight) > $weight['above_weight']) {
						$additionalPrice = floatval($weight['price']);

						break;
					}
				}

				if ($additionalPrice) {
					//multiply exceeding weight by weight unit price
					$this->method_data['cost'] += $additionalWeight * $additionalPrice;
				}
			}
		}

		if ($this->method_data['cost'] == 0) {
			$this->method_data['text']  = __('Free shipping');
		}

		$this->method_data['difference'] = max($this->method_data['free_shipping'] - $total - $this->method_data['cost'], 0);
	}

	public function getMethod(&$checkoutInfo = [], $options = []) {
		$this->method_data         = $options + $this->method_data;
		$this->method_data['cost'] = floatval($this->method_data['cost']);

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

		$this->getCost();

		return $this->method_data;
	}

	public function init() {
		//remove previous total and tax
		$this->cart->removeTotal($this->namespace);
		$this->cart->removeTax($this->namespace);
	}

	public function setMethod() {
		$view = View ::getInstance();

		if ($this->method_data['cost'] || $this->method_data['text'] || $this->method_data['difference']) {
			if ($this->method_data['free_message'] && $this->method_data['difference']) {
				$currency                       = Currency :: getInstance($options);
				$view->info['shipping-message'] = sprintf($this->method_data['free_message'], $currency->format($this->method_data['difference']));
			}

			if ($this->method_data['tax_type_id']) {
				$this->cart->addTax($this->namespace, $this->method_data['cost'], $this->method_data['tax_type_id']);
			}

			$this->cart->addTotal('shipping', $this->namespace, $this->method_data['title'] , $this->method_data['cost'], $this->method_data['text']);
		}
	}

	public function ship() {
		return true;
	}
}
