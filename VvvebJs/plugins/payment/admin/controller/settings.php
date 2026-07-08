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

namespace Vvveb\Plugins\Payment\Controller;

use function Vvveb\__;
use Vvveb\Controller\Base;
use function Vvveb\deleteSetting;
use function Vvveb\getSetting;
use function Vvveb\setMultiSetting;
use Vvveb\Sql\Payment_StatusSQL;
use Vvveb\Sql\Region_GroupSQL;
use Vvveb\Sql\Tax_TypeSQL;
use Vvveb\System\CacheManager;

class Settings extends Base {
	private $namespace = 'payment';

	function save() {
		//$validator = new Validator(["plugins.{$this->namespace}.settings"]);
		$settings  = $this->request->post['methods'] ?? false;
		$errors    = [];

		if ($settings /*&&
			($errors = $validator->validate($settings)) === true*/) {
			//$settings              = $validator->filter($settings);
			deleteSetting($this->namespace, null, $this->global['site_id']);
			$settings              = array_values($settings); //reset index
			$results               = setMultiSetting($this->namespace, $settings, $this->global['site_id']);

			CacheManager::delete();
			$this->view->success[] = __('Settings saved!');
		} else {
			$this->view->errors = $errors;
		}

		return $this->index();
	}

	function index() {
		$geoRegion = new Region_GroupSQL();
		$regions	  = $geoRegion->getAll($this->global);

		$region_group_id = [];

		foreach ($regions['region_group'] as $region_group) {
			$region_group_id[$region_group['region_group_id']] = $region_group['name'];
		}

		$this->view->region_group = $region_group_id;

		$taxTypes = new Tax_TypeSQL();
		$taxes    = $taxTypes->getAll($this->global);

		$tax_type_id = [];

		foreach ($taxes['tax_type'] as $tax) {
			$tax_type_id[$tax['tax_type_id']] = $tax['name'];
		}

		$paymentStatus  = new Payment_StatusSQL();
		$statuses       = $paymentStatus->getAll($this->global);

		$payment_status_id = [];
		foreach ($statuses['payment_status'] as $status) {
			$payment_status_id[$status['payment_status_id']] = $status['name'];
		}

		$this->view->methods         = getSetting($this->namespace, null,null, $this->global['site_id']);
		$this->view->tax_type        = $tax_type_id;
		$this->view->payment_status  = $payment_status_id;
	}
}
