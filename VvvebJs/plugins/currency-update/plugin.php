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
Name: CurrencyUpdate
Slug: currency-update
Category: tools
Url: https://www.vvveb.com
Description: Update currency rates from European Central Bank data
Author: givanz
Version: 0.1
Thumb: currency-update.svg
Author url: https://www.vvveb.com
*/
if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class CurrencyUpdatePlugin {
	function admin() {
		//add admin menu item
	}

	function app() {
	}

	function __construct() {
		if (APP == 'admin') {
			$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$currencyUpdatePlugin = new CurrencyUpdatePlugin();
