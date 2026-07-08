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

/*
Name: Default Payment
Slug: payment
Category: payment
Url: https://www.vvveb.com
Description: Add payment methods for checkout page like cod or bank transfer
Author: givanz
Version: 0.1
Thumb: cash.svg
Author url: https://www.vvveb.com
Settings: /admin/index.php?module=plugins/payment/settings
*/

use function Vvveb\getSetting;
use Vvveb\Plugins\Payment\Payment as PaymentMethod;
use function Vvveb\slugify;
use Vvveb\System\Core\Request;
use Vvveb\System\Event;
use Vvveb\System\Locale;
use Vvveb\System\Payment as PaymentApi;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class Payment {
	private $namespace = 'payment';

	function init() {
		$settings = getSetting($this->namespace, null, []);

		$payment = PaymentApi::getInstance();

		foreach ($settings as $index => $method) {
			$lang = $method['lang'][Locale :: getLanguageId()] ?? [];

			if ($lang) {
				unset($method['lang']);
				$method += $lang;
			}

			$name = slugify($lang['title'] ?? $this->namespace);
			$payment->registerMethod($name, PaymentMethod::class, $method);
		}
	}

	function admin() {
		$request = Request::getInstance();
		$module  = $request->get['module'] ?? '';

		if ($module == 'order/order') {
			$this->init();
		}
	}

	function app() {
		Event::on('Vvveb\Controller\Base', 'init', __CLASS__, function ($site) {
			$request = Request::getInstance();
			$route = $request->get['route'] ?? '';
			$module = $request->get['module'] ?? '';

			if ($route == '/checkout' || $module == 'checkout/checkout/index') {
				$this->init();
			}

			return [$site];
		});
	}

	function __construct() {
		if (Vvveb\isEditor()) {
			return;
		}

		if (APP == 'admin') {
			$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$payment = new Payment();
