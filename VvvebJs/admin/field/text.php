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

namespace Vvveb\CustomField;

class Comments extends \Vvveb\System\CustomField {
	public static $fields = [
		'default_value'           => [
			'type'  => 'text',
			'label' => 'Default Value',
			'info'  => 'Appears when creating a new post',
		],
		'placeholder'           => [
			'type'  => 'text',
			'label' => 'Placeholder Text',
			'info'  => 'Appears within the input',
		],
		'prepend'           => [
			'type'  => 'text',
			'label' => 'Prepend',
			'info'  => 'Appears before the input',
		],
		'append'           => [
			'type'  => 'text',
			'label' => 'Append',
			'info'  => 'Appears after the input',
		],
		'character_imit'           => [
			'type'  => 'text',
			'label' => 'Character Limit',
			'info'  => 'Leave blank for no limit',
		],
	];

	function results() {
	}
}
