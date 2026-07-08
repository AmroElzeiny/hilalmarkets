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

namespace Vvveb\Plugins\TwoFactorAuth\Controller;

use Vvveb\Controller\User\Base as BaseCommon;

class Base extends BaseCommon {
	protected function init2fa($siteName = 'Vvveb') {
		// in practice you would require the composer loader if it was not already part of your framework or project
		spl_autoload_register(function ($className) {
			include_once str_replace(['RobThree\\Auth', '\\'], [__DIR__ . '/../../system/lib', '/'], $className) . '.php';
		});

		// substitute your company or app name here
		$tfa    = new \RobThree\Auth\TwoFactorAuth(new \RobThree\Auth\Providers\Qr\QRServerProvider(), $siteName);

		return $tfa;
	}
}
