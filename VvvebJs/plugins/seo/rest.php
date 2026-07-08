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

namespace Vvveb\Plugins\Seo;

use Vvveb\System\Event;
use Vvveb\System\Routes;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

#[\AllowDynamicProperties]
class Rest {
	function types($types) {
		$types['post_seo'] =
	  [
	  	'name'       => 'post_seo',
	  	'properties' => [
	  		'post_id' => [
	  			'name'        => 'post_id',
	  			'description' => '',
	  			'type'        => 'ID',
	  		],
	  		'language_id' => [
	  			'name'        => 'language_id',
	  			'description' => '',
	  			'type'        => 'ID',
	  		],
	  		'name' => [
	  			'name'        => 'name',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'slug' => [
	  			'name'        => 'slug',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'content' => [
	  			'name'        => 'content',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'tag' => [
	  			'name'        => 'tag',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'meta_title' => [
	  			'name'        => 'meta_title',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'meta_description' => [
	  			'name'        => 'meta_description',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'meta_keywords' => [
	  			'name'        => 'meta_keywords',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  	],
	  ];

		$types['post_seo']['properties']['seo'] =
	  [
	  	'name' => 'seo',
	  	'type' => '[PostSeoType]',
	  ];

		$types['product_seo'] =
	  [
	  	'name'       => 'product_seo',
	  	'properties' => [
	  		'product_id' => [
	  			'name'        => 'product_id',
	  			'description' => '',
	  			'type'        => 'ID',
	  		],
	  		'language_id' => [
	  			'name'        => 'language_id',
	  			'description' => '',
	  			'type'        => 'ID',
	  		],
	  		'name' => [
	  			'name'        => 'name',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'slug' => [
	  			'name'        => 'slug',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'content' => [
	  			'name'        => 'content',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'tag' => [
	  			'name'        => 'tag',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'meta_title' => [
	  			'name'        => 'meta_title',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'meta_description' => [
	  			'name'        => 'meta_description',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'meta_keywords' => [
	  			'name'        => 'meta_keywords',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  	],
	  ];

		$types['post']['properties']['seo'] =
	  [
	  	'name' => 'seo',
	  	'type' => '[PostSeoType]',
	  ];

		$types['product']['properties']['seo'] =
	  [
	  	'name' => 'seo',
	  	'type' => '[ProductSeoType]',
	  ];

		return [$types];
	}

	function models($models) {
		return [$models];
	}

	function __construct() {
		Routes::addRoute('/rest/seo',  ['module' => 'plugins/seo/index/index']);

		Event::on('Vvveb\Controller\Schema', 'types', __CLASS__, [$this, 'types']);
		Event::on('Vvveb\Controller\Schema', 'models', __CLASS__, [$this, 'models']);
	}
}
